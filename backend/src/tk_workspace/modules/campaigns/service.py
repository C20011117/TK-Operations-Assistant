"""找人任务：一个任务推广一个产品；每个站点是一个执行单元（campaign_market），切片阶段一个任务只有一个站点。

规则：
- 任务创建时引用产品的当前确认版本，之后产品再修改也不影响本任务，除非用户主动“更新到新版本”。
- 币种、时区来自站点目录，不能手填。
- 条件先存草稿，确认时通过就绪检查后冻结为新版本；有阻断项时不能确认（M2 的匹配只能从已确认的任务启动）。
- 复制任务到其他站点时，条件复制为新草稿，预算不复制（币种不同）。
"""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from tk_workspace.modules.campaigns.criteria import Criterion, SearchInput, normalize, normalize_search
from tk_workspace.modules.campaigns.schemas import (
    CampaignCreate,
    CampaignDetail,
    CampaignDuplicate,
    CampaignMarketView,
    CampaignSummary,
    CampaignUpdate,
    CriteriaIn,
    CriteriaVersion,
    CriteriaVersionSummary,
    Issue,
    MarketSettingsIn,
    ProductRef,
    Readiness,
)
from tk_workspace.modules.knowledge.schemas import MarketTerm
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso


class CampaignError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _nf(what: str = "任务") -> CampaignError:
    return CampaignError("not_found", f"{what}不存在", 404)


# ---------- 读取 ----------


def _market(s: Session, code: str):
    return s.execute(text("SELECT * FROM markets WHERE market_code = :c"), {"c": code}).mappings().first()


def _criteria_version(s: Session, row) -> CriteriaVersion:
    pv_no = s.execute(
        text("SELECT version_no FROM product_versions WHERE id = :id"), {"id": row["product_version_id"]}
    ).scalar_one()
    return CriteriaVersion(
        id=row["id"],
        version_no=row["version_no"],
        status=row["status"],
        product_version_id=row["product_version_id"],
        product_version_no=pv_no,
        criteria=[Criterion(**c) for c in json.loads(row["criteria"])],
        search=SearchInput(**json.loads(row["search"])),
        created_at=row["created_at"],
        confirmed_at=row["confirmed_at"],
    )


def _price_term(s: Session, product_version_id: str, market_code: str) -> MarketTerm | None:
    t = (
        s.execute(
            text("SELECT * FROM product_market_terms WHERE product_version_id = :v AND market_code = :m"),
            {"v": product_version_id, "m": market_code},
        )
        .mappings()
        .first()
    )
    if not t:
        return None
    return MarketTerm(**{k: t[k] for k in MarketTerm.model_fields})


def _readiness(
    s: Session, cm, market, criteria_row, price_term: MarketTerm | None, latest_pv_id: str | None
) -> Readiness:
    blockers: list[Issue] = []
    warnings: list[Issue] = []
    manual_only = market["data_status"] == "manual_import_only"

    pv_status = s.execute(
        text("SELECT status FROM product_versions WHERE id = :id"), {"id": cm["product_version_id"]}
    ).scalar_one()
    if pv_status == "draft":
        blockers.append(Issue(code="product_version_unconfirmed", message="引用的产品版本尚未确认"))
    if latest_pv_id and latest_pv_id != cm["product_version_id"]:
        warnings.append(Issue(code="product_version_outdated", message="产品已有更新的确认版本，可选择更新"))

    if price_term is None:
        blockers.append(
            Issue(
                code="price_term_missing",
                message=f"产品版本中没有 {market['name_zh']} 站点的价格条款，请先在产品档案中补充",
            )
        )
    elif price_term.price_status == "unknown":
        warnings.append(
            Issue(code="price_unknown", message="该站点价格为“未知”，推荐时无法判断价格带是否匹配")
        )

    if cm["target_list_size"] is None:
        blockers.append(Issue(code="target_list_size_missing", message="请填写目标名单数量"))
    if cm["budget_min"] is None and cm["budget_max"] is None:
        warnings.append(Issue(code="budget_missing", message="未填写预算范围，费用将按“未知”处理"))

    if manual_only:
        warnings.append(
            Issue(
                code="manual_import_only", message=f"{market['name_zh']} 没有 FastMoss 数据，候选只能人工导入"
            )
        )
    elif cm["cost_cap_credits"] is None:
        blockers.append(Issue(code="cost_cap_missing", message="请设置本任务的 FastMoss 额度上限"))

    if criteria_row is None:
        blockers.append(Issue(code="criteria_missing", message="请设置找人条件"))
    else:
        crit = json.loads(criteria_row["criteria"])
        search = json.loads(criteria_row["search"])
        if not crit and not search.get("keywords"):
            blockers.append(Issue(code="criteria_empty", message="至少填写一个搜索关键词或一个条件"))
        if crit and not any(c["hardness"] == "hard" for c in crit):
            warnings.append(Issue(code="no_hard_criteria", message="没有硬条件，所有候选都会进入推荐"))
        not_provided_hard = [
            c for c in crit if c["hardness"] == "hard" and c["field_key"] == "content_language"
        ]
        if not_provided_hard:
            warnings.append(
                Issue(
                    code="hard_needs_manual",
                    message="“内容语言”是硬条件但 FastMoss 没有此数据，候选都会是“待核实”",
                )
            )
    return Readiness(ready=not blockers, blockers=blockers, warnings=warnings)


def _market_view(s: Session, cm) -> CampaignMarketView:
    market = _market(s, cm["market_code"])
    rows = (
        s.execute(
            text("SELECT * FROM criteria_versions WHERE campaign_market_id = :id ORDER BY version_no DESC"),
            {"id": cm["id"]},
        )
        .mappings()
        .all()
    )
    draft = next((r for r in rows if r["status"] == "draft"), None)
    current = next((r for r in rows if r["status"] == "confirmed"), None)
    product_id, pv_no = s.execute(
        text("SELECT product_id, version_no FROM product_versions WHERE id = :id"),
        {"id": cm["product_version_id"]},
    ).one()
    latest_pv_id = s.execute(
        text("SELECT current_version_id FROM products WHERE id = :id"), {"id": product_id}
    ).scalar()
    latest_no = (
        s.execute(
            text("SELECT version_no FROM product_versions WHERE id = :id"), {"id": latest_pv_id}
        ).scalar()
        if latest_pv_id
        else None
    )
    price = _price_term(s, cm["product_version_id"], cm["market_code"])
    pv_nos = dict(
        s.execute(
            text(
                "SELECT cv.id, pv.version_no FROM criteria_versions cv JOIN product_versions pv"
                " ON pv.id = cv.product_version_id WHERE cv.campaign_market_id = :id"
            ),
            {"id": cm["id"]},
        ).all()
    )
    return CampaignMarketView(
        id=cm["id"],
        market_code=cm["market_code"],
        market_name_zh=market["name_zh"],
        market_data_status=market["data_status"],
        fastmoss_region=market["fastmoss_region"],
        content_languages=json.loads(market["content_languages"]),
        time_zone=cm["time_zone"],
        reporting_currency=cm["reporting_currency"],
        target_list_size=cm["target_list_size"],
        budget_min=cm["budget_min"],
        budget_max=cm["budget_max"],
        cost_cap_credits=cm["cost_cap_credits"],
        status=cm["status"],
        confirmed_at=cm["confirmed_at"],
        product_version_id=cm["product_version_id"],
        product_version_no=pv_no,
        latest_product_version_no=latest_no,
        price_term=price,
        criteria_draft=_criteria_version(s, draft) if draft else None,
        criteria_current=_criteria_version(s, current) if current else None,
        criteria_history=[
            CriteriaVersionSummary(
                id=r["id"],
                version_no=r["version_no"],
                status=r["status"],
                product_version_no=pv_nos[r["id"]],
                confirmed_at=r["confirmed_at"],
            )
            for r in rows
        ],
        readiness=_readiness(s, cm, market, draft or current, price, latest_pv_id),
    )


def _detail(s: Session, campaign_id: str) -> CampaignDetail:
    c = s.execute(text("SELECT * FROM campaigns WHERE id = :id"), {"id": campaign_id}).mappings().first()
    if not c:
        raise _nf()
    p = (
        s.execute(text("SELECT id, sku, name FROM products WHERE id = :id"), {"id": c["product_id"]})
        .mappings()
        .one()
    )
    cms = (
        s.execute(
            text("SELECT * FROM campaign_markets WHERE campaign_id = :id ORDER BY created_at"),
            {"id": campaign_id},
        )
        .mappings()
        .all()
    )
    return CampaignDetail(
        id=c["id"],
        name=c["name"],
        goal=c["goal"],
        collaboration_type=c["collaboration_type"],
        start_date=c["start_date"],
        end_date=c["end_date"],
        notes=c["notes"],
        status=c["status"],
        product=ProductRef(**p),
        markets=[_market_view(s, cm) for cm in cms],
        created_at=c["created_at"],
        updated_at=c["updated_at"],
    )


def get_campaign(campaign_id: str) -> CampaignDetail:
    with tx() as s:
        return _detail(s, campaign_id)


def list_campaigns(product_id: str | None = None, include_archived: bool = False) -> list[CampaignSummary]:
    where, params = [], {}
    if not include_archived:
        where.append("c.status = 'active'")
    if product_id:
        where.append("c.product_id = :p")
        params["p"] = product_id
    sql = f"""SELECT c.id, c.name, c.goal, c.status, c.start_date, c.end_date, c.updated_at,
                     p.name AS product_name, p.sku AS product_sku,
                     cm.market_code, m.name_zh AS market_name_zh, cm.reporting_currency, cm.time_zone,
                     cm.status AS market_status
              FROM campaigns c
              JOIN products p ON p.id = c.product_id
              JOIN campaign_markets cm ON cm.campaign_id = c.id
              JOIN markets m ON m.market_code = cm.market_code
              {"WHERE " + " AND ".join(where) if where else ""}
              ORDER BY c.updated_at DESC"""  # noqa: S608 — where 子句只由固定片段拼接
    with tx() as s:
        return [CampaignSummary(**r) for r in s.execute(text(sql), params).mappings().all()]


# ---------- 写入 ----------


def _get_cm(s: Session, cm_id: str):
    cm = s.execute(text("SELECT * FROM campaign_markets WHERE id = :id"), {"id": cm_id}).mappings().first()
    if not cm:
        raise _nf("站点任务")
    st = s.execute(
        text("SELECT status FROM campaigns WHERE id = :id"), {"id": cm["campaign_id"]}
    ).scalar_one()
    if st != "active":
        raise CampaignError("archived", "任务已归档，不能修改")
    return cm


def _touch(s: Session, campaign_id: str, now: str) -> None:
    s.execute(text("UPDATE campaigns SET updated_at = :now WHERE id = :id"), {"now": now, "id": campaign_id})


def _insert_cm(
    s: Session,
    campaign_id: str,
    market_code: str,
    product_version_id: str,
    settings: MarketSettingsIn,
    now: str,
) -> str:
    market = _market(s, market_code)
    if not market:
        raise CampaignError("unknown_market", f"未知站点：{market_code}", 422)
    cm_id = str(uuid.uuid4())
    s.execute(
        text(
            """INSERT INTO campaign_markets (id, campaign_id, market_code, time_zone, reporting_currency, product_version_id,
                   target_list_size, budget_min, budget_max, cost_cap_credits, status, created_at, updated_at)
               VALUES (:id, :c, :m, :tz, :cur, :pv, :t, :bmin, :bmax, :cap, 'draft', :now, :now)"""
        ),
        {
            "id": cm_id,
            "c": campaign_id,
            "m": market_code,
            "tz": market["default_time_zone"],
            "cur": market["settlement_currency"],
            "pv": product_version_id,
            "t": settings.target_list_size,
            "bmin": settings.budget_min,
            "bmax": settings.budget_max,
            "cap": settings.cost_cap_credits,
            "now": now,
        },
    )
    return cm_id


def _write_criteria_draft(
    s: Session, cm, body: CriteriaIn, now: str, product_version_id: str | None = None
) -> None:
    market = _market(s, cm["market_code"])
    criteria, issues = normalize(body.criteria, json.loads(market["content_languages"]))
    search, s_issues = normalize_search(body.search)
    issues += s_issues
    if issues:
        raise CampaignError(
            "invalid_criteria",
            "条件有误：" + "；".join(i.message for i in issues),
            422,
            {"issues": [i.model_dump() for i in issues]},
        )
    crit_json = json.dumps([c.model_dump(mode="json") for c in criteria], ensure_ascii=False)
    search_json = search.model_dump_json()
    pv = product_version_id or cm["product_version_id"]
    draft_id = s.execute(
        text("SELECT id FROM criteria_versions WHERE campaign_market_id = :id AND status = 'draft'"),
        {"id": cm["id"]},
    ).scalar()
    if draft_id:
        s.execute(
            text(
                "UPDATE criteria_versions SET criteria = :c, search = :s, product_version_id = :pv, updated_at = :now"
                " WHERE id = :id"
            ),
            {"c": crit_json, "s": search_json, "pv": pv, "now": now, "id": draft_id},
        )
    else:
        no = s.execute(
            text(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM criteria_versions WHERE campaign_market_id = :id"
            ),
            {"id": cm["id"]},
        ).scalar_one()
        s.execute(
            text(
                """INSERT INTO criteria_versions (id, campaign_market_id, version_no, product_version_id, criteria, search,
                       status, created_at, updated_at) VALUES (:id, :cm, :no, :pv, :c, :s, 'draft', :now, :now)"""
            ),
            {
                "id": str(uuid.uuid4()),
                "cm": cm["id"],
                "no": no,
                "pv": pv,
                "c": crit_json,
                "s": search_json,
                "now": now,
            },
        )
    s.execute(
        text("UPDATE campaign_markets SET status = 'draft', updated_at = :now WHERE id = :id"),
        {"now": now, "id": cm["id"]},
    )


def create_campaign(body: CampaignCreate) -> CampaignDetail:
    now = utcnow_iso()
    with tx() as s:
        p = s.execute(
            text("SELECT status, current_version_id FROM products WHERE id = :id"), {"id": body.product_id}
        ).first()
        if not p:
            raise _nf("产品")
        if p[0] != "active":
            raise CampaignError("product_archived", "产品已归档")
        if not p[1]:
            raise CampaignError("product_unconfirmed", "产品还没有确认的版本，请先在产品档案中确认资料")
        cid = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO campaigns (id, product_id, name, goal, collaboration_type, start_date, end_date, notes,
                       status, created_at, updated_at)
                   VALUES (:id, :p, :n, :g, :ct, :sd, :ed, :notes, 'active', :now, :now)"""
            ),
            {
                "id": cid,
                "p": body.product_id,
                "n": body.name,
                "g": body.goal,
                "ct": body.collaboration_type,
                "sd": body.start_date.isoformat() if body.start_date else None,
                "ed": body.end_date.isoformat() if body.end_date else None,
                "notes": body.notes,
                "now": now,
            },
        )
        cm_id = _insert_cm(s, cid, body.market_code, p[1], body.market, now)
        cm = s.execute(text("SELECT * FROM campaign_markets WHERE id = :id"), {"id": cm_id}).mappings().one()
        _write_criteria_draft(s, cm, CriteriaIn(), now)
        return _detail(s, cid)


def update_campaign(campaign_id: str, body: CampaignUpdate) -> CampaignDetail:
    with tx() as s:
        st = s.execute(text("SELECT status FROM campaigns WHERE id = :id"), {"id": campaign_id}).scalar()
        if st is None:
            raise _nf()
        if st != "active":
            raise CampaignError("archived", "任务已归档，不能修改")
        s.execute(
            text(
                """UPDATE campaigns SET name = :n, goal = :g, collaboration_type = :ct, start_date = :sd, end_date = :ed,
                       notes = :notes, updated_at = :now WHERE id = :id"""
            ),
            {
                "id": campaign_id,
                "n": body.name,
                "g": body.goal,
                "ct": body.collaboration_type,
                "sd": body.start_date.isoformat() if body.start_date else None,
                "ed": body.end_date.isoformat() if body.end_date else None,
                "notes": body.notes,
                "now": utcnow_iso(),
            },
        )
        return _detail(s, campaign_id)


def update_market_settings(cm_id: str, body: MarketSettingsIn) -> CampaignDetail:
    """修改名单数量、预算或额度上限后，任务回到草稿状态，需要重新确认。"""
    now = utcnow_iso()
    with tx() as s:
        cm = _get_cm(s, cm_id)
        s.execute(
            text(
                """UPDATE campaign_markets SET target_list_size = :t, budget_min = :bmin, budget_max = :bmax,
                       cost_cap_credits = :cap, status = 'draft', updated_at = :now WHERE id = :id"""
            ),
            {
                "t": body.target_list_size,
                "bmin": body.budget_min,
                "bmax": body.budget_max,
                "cap": body.cost_cap_credits,
                "now": now,
                "id": cm_id,
            },
        )
        _touch(s, cm["campaign_id"], now)
        return _detail(s, cm["campaign_id"])


def save_criteria(cm_id: str, body: CriteriaIn) -> CampaignDetail:
    now = utcnow_iso()
    with tx() as s:
        cm = _get_cm(s, cm_id)
        _write_criteria_draft(s, cm, body, now)
        _touch(s, cm["campaign_id"], now)
        return _detail(s, cm["campaign_id"])


def discard_criteria_draft(cm_id: str) -> CampaignDetail:
    now = utcnow_iso()
    with tx() as s:
        cm = _get_cm(s, cm_id)
        has_current = s.execute(
            text("SELECT 1 FROM criteria_versions WHERE campaign_market_id = :id AND status = 'confirmed'"),
            {"id": cm_id},
        ).first()
        if not has_current:
            raise CampaignError("no_confirmed", "还没有确认过的条件，草稿不能放弃")
        s.execute(
            text("DELETE FROM criteria_versions WHERE campaign_market_id = :id AND status = 'draft'"),
            {"id": cm_id},
        )
        _touch(s, cm["campaign_id"], now)
        return _detail(s, cm["campaign_id"])


def use_latest_product_version(cm_id: str) -> CampaignDetail:
    """把任务切换到产品的最新确认版本：条件复制为新草稿，任务回到草稿状态，需重新确认。"""
    now = utcnow_iso()
    with tx() as s:
        cm = _get_cm(s, cm_id)
        product_id = s.execute(
            text("SELECT product_id FROM campaigns WHERE id = :id"), {"id": cm["campaign_id"]}
        ).scalar_one()
        latest = s.execute(
            text("SELECT current_version_id FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        if not latest or latest == cm["product_version_id"]:
            raise CampaignError("already_latest", "已经是产品的最新确认版本")
        base = (
            s.execute(
                text(
                    "SELECT * FROM criteria_versions WHERE campaign_market_id = :id AND status IN ('draft','confirmed')"
                    " ORDER BY version_no DESC LIMIT 1"
                ),
                {"id": cm_id},
            )
            .mappings()
            .first()
        )
        body = (
            CriteriaIn(
                criteria=[Criterion(**c) for c in json.loads(base["criteria"])],
                search=SearchInput(**json.loads(base["search"])),
            )
            if base
            else CriteriaIn()
        )
        s.execute(
            text("UPDATE campaign_markets SET product_version_id = :pv, updated_at = :now WHERE id = :id"),
            {"pv": latest, "now": now, "id": cm_id},
        )
        _write_criteria_draft(s, cm, body, now, product_version_id=latest)
        _touch(s, cm["campaign_id"], now)
        return _detail(s, cm["campaign_id"])


def confirm_market(cm_id: str) -> CampaignDetail:
    now = utcnow_iso()
    with tx() as s:
        cm = _get_cm(s, cm_id)
        view = _market_view(s, cm)
        if not view.readiness.ready:
            raise CampaignError(
                "not_ready",
                "还有缺项，不能确认：" + "；".join(b.message for b in view.readiness.blockers),
                422,
                {"blockers": [b.model_dump() for b in view.readiness.blockers]},
            )
        draft = s.execute(
            text("SELECT id FROM criteria_versions WHERE campaign_market_id = :id AND status = 'draft'"),
            {"id": cm_id},
        ).scalar()
        current_id = cm["current_criteria_version_id"]
        if draft:
            s.execute(
                text(
                    "UPDATE criteria_versions SET status = 'superseded', updated_at = :now"
                    " WHERE campaign_market_id = :id AND status = 'confirmed'"
                ),
                {"now": now, "id": cm_id},
            )
            s.execute(
                text(
                    "UPDATE criteria_versions SET status = 'confirmed', confirmed_at = :now, product_version_id = :pv,"
                    " updated_at = :now WHERE id = :id"
                ),
                {"now": now, "pv": cm["product_version_id"], "id": draft},
            )
            current_id = draft
        s.execute(
            text(
                "UPDATE campaign_markets SET status = 'ready', confirmed_at = :now, current_criteria_version_id = :cv,"
                " updated_at = :now WHERE id = :id"
            ),
            {"now": now, "cv": current_id, "id": cm_id},
        )
        _touch(s, cm["campaign_id"], now)
        return _detail(s, cm["campaign_id"])


def duplicate_campaign(campaign_id: str, body: CampaignDuplicate) -> CampaignDetail:
    now = utcnow_iso()
    with tx() as s:
        src = (
            s.execute(text("SELECT * FROM campaigns WHERE id = :id"), {"id": campaign_id}).mappings().first()
        )
        if not src:
            raise _nf()
        latest = s.execute(
            text("SELECT current_version_id FROM products WHERE id = :id AND status = 'active'"),
            {"id": src["product_id"]},
        ).scalar()
        if not latest:
            raise CampaignError("product_unavailable", "产品已归档或没有确认版本")
        src_cm = (
            s.execute(
                text("SELECT * FROM campaign_markets WHERE campaign_id = :id LIMIT 1"), {"id": campaign_id}
            )
            .mappings()
            .one()
        )
        market = _market(s, body.market_code)
        if not market:
            raise CampaignError("unknown_market", f"未知站点：{body.market_code}", 422)
        cid = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO campaigns (id, product_id, name, goal, collaboration_type, start_date, end_date, notes,
                       status, created_at, updated_at)
                   VALUES (:id, :p, :n, :g, :ct, :sd, :ed, :notes, 'active', :now, :now)"""
            ),
            {
                "id": cid,
                "p": src["product_id"],
                "n": body.name or f"{src['name']}（{market['name_zh']}）",
                "g": src["goal"],
                "ct": src["collaboration_type"],
                "sd": src["start_date"],
                "ed": src["end_date"],
                "notes": src["notes"],
                "now": now,
            },
        )
        settings = MarketSettingsIn(
            target_list_size=src_cm["target_list_size"], cost_cap_credits=src_cm["cost_cap_credits"]
        )  # 预算不复制：币种不同
        cm_id = _insert_cm(s, cid, body.market_code, latest, settings, now)
        base = (
            s.execute(
                text(
                    "SELECT * FROM criteria_versions WHERE campaign_market_id = :id AND status IN ('draft','confirmed')"
                    " ORDER BY version_no DESC LIMIT 1"
                ),
                {"id": src_cm["id"]},
            )
            .mappings()
            .first()
        )
        # 内容语言与金额类条件随站点（语言、币种）变化，不复制，需要在新站点重新设置
        site_specific = {"content_language", "day28_gmv"}
        crit = [
            Criterion(**{**c, "criterion_id": str(uuid.uuid4())})
            for c in (json.loads(base["criteria"]) if base else [])
            if c["field_key"] not in site_specific
        ]
        search = SearchInput(**json.loads(base["search"])) if base else SearchInput()
        cm = s.execute(text("SELECT * FROM campaign_markets WHERE id = :id"), {"id": cm_id}).mappings().one()
        _write_criteria_draft(s, cm, CriteriaIn(criteria=crit, search=search), now)
        return _detail(s, cid)


def set_archived(campaign_id: str, archived: bool) -> CampaignDetail:
    with tx() as s:
        n = s.execute(
            text("UPDATE campaigns SET status = :st, updated_at = :now WHERE id = :id"),
            {"st": "archived" if archived else "active", "now": utcnow_iso(), "id": campaign_id},
        ).rowcount
        if not n:
            raise _nf()
        return _detail(s, campaign_id)
