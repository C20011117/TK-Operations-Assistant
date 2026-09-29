"""匹配运行：启动（幂等）、人工导入、查询、取消。"""

import csv
import io
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tk_workspace.integrations.fastmoss.creator_search import to_decimal
from tk_workspace.modules.matching import pipeline, provider
from tk_workspace.modules.matching.schemas import (
    ImportProblem,
    ManualImportIn,
    ProviderCallView,
    RecommendationCard,
    RecommendationsView,
    RunView,
    SnapshotView,
)
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs import service as jobs

ACTIVE = ("queued", "running")


class MatchingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _sync_stale(s: Session) -> None:
    """任务已结束（例如应用多次中断后判为失败）但运行仍显示进行中时，同步运行状态。"""
    s.execute(
        text(
            """UPDATE matching_runs
               SET status = CASE (SELECT j.status FROM jobs j WHERE j.id = matching_runs.job_id)
                              WHEN 'cancelled' THEN 'cancelled' ELSE 'failed' END,
                   error = COALESCE(error, (SELECT j.last_error FROM jobs j WHERE j.id = matching_runs.job_id)),
                   finished_at = :n, updated_at = :n
               WHERE status IN ('queued','running')
                 AND job_id IN (SELECT id FROM jobs WHERE status IN ('failed','cancelled','succeeded','partial'))"""
        ),
        {"n": utcnow_iso()},
    )


def _run_view(s: Session, run_id: str, with_calls: bool = False) -> RunView:
    _sync_stale(s)
    r = (
        s.execute(
            text(
                """SELECT r.*, cv.version_no AS cv_no, pv.version_no AS pv_no FROM matching_runs r
               JOIN criteria_versions cv ON cv.id=r.criteria_version_id
               JOIN product_versions pv ON pv.id=r.product_version_id WHERE r.id=:id"""
            ),
            {"id": run_id},
        )
        .mappings()
        .first()
    )
    if r is None:
        raise MatchingError("not_found", "匹配运行不存在", 404)
    calls = []
    if with_calls:
        for c in s.execute(
            text("SELECT * FROM provider_calls WHERE run_id=:r ORDER BY created_at"), {"r": run_id}
        ).mappings():
            p = json.loads(c["params"])
            calls.append(
                ProviderCallView(
                    id=c["id"],
                    operation=c["operation"],
                    keywords=p.get("keywords"),
                    page=p.get("page"),
                    status=c["status"],
                    credit_cost=c["credit_cost"],
                    result_count=c["result_count"],
                    total=c["total"],
                    remaining_credits=c["remaining_credits"],
                    error=c["error"],
                    created_at=c["created_at"],
                )
            )
    return RunView(
        id=r["id"],
        campaign_market_id=r["campaign_market_id"],
        source=r["source"],
        market_code=r["market_code"],
        provider_region=r["provider_region"],
        reporting_currency=r["reporting_currency"],
        status=r["status"],
        stage=r["stage"],
        target_list_size=r["target_list_size"],
        cost_cap_credits=r["cost_cap_credits"],
        credits_used=r["credits_used"],
        llm_input_tokens=r["llm_input_tokens"],
        llm_output_tokens=r["llm_output_tokens"],
        counters=json.loads(r["counters"] or "{}"),
        stop_reason=r["stop_reason"],
        stop_message=pipeline.STOP_MESSAGES.get(r["stop_reason"] or ""),
        error=json.loads(r["error"]) if r["error"] else None,
        criteria_version_no=r["cv_no"],
        product_version_no=r["pv_no"],
        created_at=r["created_at"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
        calls=calls,
    )


def _load_cm(s: Session, cm_id: str) -> Any:
    cm = (
        s.execute(
            text(
                """SELECT cm.*, c.status AS campaign_status, m.data_status, m.fastmoss_region, m.name_zh
               FROM campaign_markets cm JOIN campaigns c ON c.id=cm.campaign_id
               JOIN markets m ON m.market_code=cm.market_code WHERE cm.id=:id"""
            ),
            {"id": cm_id},
        )
        .mappings()
        .first()
    )
    if cm is None:
        raise MatchingError("not_found", "任务站点不存在", 404)
    if cm["campaign_status"] != "active":
        raise MatchingError("campaign_archived", "任务已归档，不能启动")
    if cm["status"] != "ready" or not cm["current_criteria_version_id"]:
        raise MatchingError("not_confirmed", "任务还没有确认（或确认后又修改过），请先在就绪检查中确认")
    if not cm["target_list_size"]:
        raise MatchingError("not_ready", "缺少目标名单数量")
    return cm


def _active_run(s: Session, cm_id: str) -> str | None:
    _sync_stale(s)
    return s.execute(
        text(
            "SELECT id FROM matching_runs WHERE campaign_market_id=:c AND status IN ('queued','running') LIMIT 1"
        ),
        {"c": cm_id},
    ).scalar()


def _idem(s: Session, action: str, key: str, payload: dict[str, Any]) -> str | None:
    """返回已存在的 run_id（相同请求重放），内容不同则冲突。"""
    rhash = jobs.request_hash(payload)
    row = s.execute(
        text("SELECT request_hash, resource_id FROM idempotency_records WHERE action=:a AND key=:k"),
        {"a": action, "k": key},
    ).first()
    if row is None:
        return None
    if row.request_hash != rhash:
        raise MatchingError("idempotency_conflict", "同一个 Idempotency-Key 已用于不同内容的请求", 422)
    return row.resource_id


def _record_idem(s: Session, action: str, key: str, payload: dict[str, Any], run_id: str, now: str) -> None:
    s.execute(
        text(
            """INSERT INTO idempotency_records (id, action, key, request_hash, resource_id, created_at)
               VALUES (:id, :a, :k, :h, :r, :n)"""
        ),
        {
            "id": str(uuid.uuid4()),
            "a": action,
            "k": key,
            "h": jobs.request_hash(payload),
            "r": run_id,
            "n": now,
        },
    )


def _insert_run(s: Session, cm: Any, source: str, now: str) -> str:
    run_id = str(uuid.uuid4())
    s.execute(
        text(
            """INSERT INTO matching_runs (id, campaign_market_id, criteria_version_id, product_version_id,
                   market_code, source, provider_region, reporting_currency, status, target_list_size,
                   cost_cap_credits, versions, created_at, updated_at)
               VALUES (:id, :cm, :cv, :pv, :m, :src, :reg, :cur, 'queued', :t, :cap, :v, :n, :n)"""
        ),
        {
            "id": run_id,
            "cm": cm["id"],
            "cv": cm["current_criteria_version_id"],
            "pv": cm["product_version_id"],
            "m": cm["market_code"],
            "src": source,
            "reg": cm["fastmoss_region"] if source == "fastmoss" else None,
            "cur": cm["reporting_currency"],
            "t": cm["target_list_size"],
            "cap": cm["cost_cap_credits"] if source == "fastmoss" else None,
            "v": json.dumps(
                {"graph": pipeline.GRAPH_VERSION, "ranking": pipeline.RANKING_VERSION},
                ensure_ascii=False,
            ),
            "n": now,
        },
    )
    return run_id


def _enqueue(s: Session, run_id: str) -> None:
    job_id = jobs.enqueue_in_tx(s, "matching.run", {"run_id": run_id})
    s.execute(text("UPDATE matching_runs SET job_id=:j WHERE id=:id"), {"j": job_id, "id": run_id})


def start_run(cm_id: str, idem_key: str) -> tuple[RunView, bool]:
    action = f"matching.start:{cm_id}"
    now = utcnow_iso()
    with tx() as s:
        cm = _load_cm(s, cm_id)
        payload = {
            "cm": cm_id,
            "criteria": cm["current_criteria_version_id"],
            "product": cm["product_version_id"],
        }
        existing = _idem(s, action, idem_key, payload)
        if existing:
            return _run_view(s, existing), False
        if cm["data_status"] == "manual_import_only" or not cm["fastmoss_region"]:
            raise MatchingError(
                "manual_import_only",
                f"{cm['name_zh']}没有 FastMoss 数据，只能人工导入候选名单",
            )
        if not cm["cost_cap_credits"]:
            raise MatchingError("not_ready", "缺少 FastMoss 额度上限")
        active = _active_run(s, cm_id)
        if active:
            raise MatchingError("run_in_progress", "该任务已有正在进行的匹配", extra={"run_id": active})
        if provider.transport_name() == "mcp":
            from tk_workspace.modules.settings.service import get_fastmoss_key

            if not get_fastmoss_key():
                raise MatchingError("fastmoss_not_configured", "还没有填写 FastMoss API Key，请到设置页填写")
        run_id = _insert_run(s, cm, "fastmoss", now)
        _enqueue(s, run_id)
        _record_idem(s, action, idem_key, payload, run_id, now)
        view = _run_view(s, run_id)
    jobs.job_enqueued.set()
    return view, True


# ---------------- 人工导入 ----------------

_HEADERS = {
    "unique_id": ("unique_id", "username", "handle", "用户名", "账号", "达人账号"),
    "nickname": ("nickname", "昵称"),
    "follower_count": ("follower_count", "followers", "粉丝数"),
    "day28_gmv": ("day28_gmv", "gmv", "近28天gmv", "近 28 天 gmv"),
    "day28_units_sold": ("day28_units_sold", "units_sold", "近28天销量", "近 28 天销量"),
    "video_avg_play_count": ("video_avg_play_count", "avg_views", "平均播放", "视频平均播放"),
    "engagement_rate": ("engagement_rate", "互动率"),
    "has_email": ("has_email", "有邮箱", "有联系邮箱"),
    "currency": ("currency", "币种"),
}
MAX_IMPORT_ROWS = 500


def parse_csv(raw: str) -> tuple[list[dict[str, Any]], list[ImportProblem]]:
    text_in = raw.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text_in))
    rows = list(reader)
    problems: list[ImportProblem] = []
    if not rows:
        return [], [ImportProblem(line=1, message="文件是空的")]
    header = [h.strip().lower() for h in rows[0]]
    col: dict[str, int] = {}
    for key, names in _HEADERS.items():
        for i, h in enumerate(header):
            if h in names:
                col[key] = i
                break
    if "unique_id" not in col:
        return [], [ImportProblem(line=1, message="缺少用户名列（unique_id / 用户名）")]
    records, seen = [], set()
    for n, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        if len(records) >= MAX_IMPORT_ROWS:
            problems.append(ImportProblem(line=n, message=f"超过 {MAX_IMPORT_ROWS} 行，其余未导入"))
            break

        def get(key: str, row: list[str] = row) -> str | None:
            i = col.get(key)
            v = row[i].strip() if i is not None and i < len(row) else ""
            return v or None

        handle = (get("unique_id") or "").lstrip("@").strip()
        if not handle:
            problems.append(ImportProblem(line=n, message="用户名为空，已跳过"))
            continue
        if handle.lower() in seen:
            problems.append(ImportProblem(line=n, message=f"@{handle} 重复，已跳过"))
            continue
        seen.add(handle.lower())
        metrics = {}
        for key in (
            "follower_count",
            "day28_gmv",
            "day28_units_sold",
            "video_avg_play_count",
            "engagement_rate",
        ):
            raw_v = get(key)
            val = to_decimal(raw_v.rstrip("%")) if raw_v else None
            if raw_v and val is None:
                problems.append(ImportProblem(line=n, message=f"@{handle} 的 {key} 不是数字，按未知处理"))
            metrics[key] = {"value": val, "raw_type": "csv" if raw_v else None}
        for key in ("total_units_sold", "video_units_sold"):
            metrics[key] = {"value": None, "raw_type": None}
        he = (get("has_email") or "").lower()
        records.append(
            {
                "provider_uid": None,
                "unique_id": handle,
                "nickname": get("nickname"),
                "region": None,
                "currency": (get("currency") or "").upper()[:3] or None,
                "profile_text": None,
                "metrics": metrics,
                "flags": {
                    "is_ecommerce_creator": None,
                    "has_email": True
                    if he in ("1", "true", "yes", "是")
                    else False
                    if he in ("0", "false", "no", "否")
                    else None,
                    "creator_type": None,
                },
                "audience": {"top_age": None},
            }
        )
    return records, problems


def import_candidates(
    cm_id: str, body: ManualImportIn, idem_key: str
) -> tuple[RunView, bool, list[ImportProblem]]:
    action = f"matching.import:{cm_id}"
    now = utcnow_iso()
    records, problems = parse_csv(body.csv)
    with tx() as s:
        cm = _load_cm(s, cm_id)
        payload = {
            "cm": cm_id,
            "criteria": cm["current_criteria_version_id"],
            "csv": jobs.request_hash({"c": body.csv}),
        }
        existing = _idem(s, action, idem_key, payload)
        if existing:
            return _run_view(s, existing), False, problems
        if not records:
            raise MatchingError(
                "import_empty", "没有可导入的候选", 422, {"problems": [p.model_dump() for p in problems]}
            )
        active = _active_run(s, cm_id)
        if active:
            raise MatchingError("run_in_progress", "该任务已有正在进行的匹配", extra={"run_id": active})
        run_id = _insert_run(s, cm, "manual_import", now)
        for pos, rec in enumerate(records, start=1):
            pipeline.upsert_candidate(
                s, run_id, rec, {"source": "manual_import", "file": body.filename[:100], "row": pos}, now
            )
        _enqueue(s, run_id)
        _record_idem(s, action, idem_key, payload, run_id, now)
        view = _run_view(s, run_id)
    jobs.job_enqueued.set()
    return view, True, problems


# ---------------- 查询 / 取消 ----------------


def list_runs(cm_id: str) -> list[RunView]:
    with tx() as s:
        ids = (
            s.execute(
                text(
                    "SELECT id FROM matching_runs WHERE campaign_market_id=:c ORDER BY created_at DESC LIMIT 20"
                ),
                {"c": cm_id},
            )
            .scalars()
            .all()
        )
        return [_run_view(s, i) for i in ids]


def get_run(run_id: str) -> RunView:
    with tx() as s:
        return _run_view(s, run_id, with_calls=True)


def get_recommendations(run_id: str) -> RecommendationsView:
    with tx() as s:
        run = _run_view(s, run_id, with_calls=True)
        snap = (
            s.execute(text("SELECT * FROM recommendation_snapshots WHERE run_id=:r"), {"r": run_id})
            .mappings()
            .first()
        )
        if snap is None:
            return RecommendationsView(run=run, snapshot=None, cards=[])
        items = (
            s.execute(
                text(
                    """SELECT card FROM recommendation_items WHERE snapshot_id=:s
                   ORDER BY CASE group_key WHEN 'qualified' THEN 0 WHEN 'needs_verification' THEN 1 ELSE 2 END, rank"""
                ),
                {"s": snap["id"]},
            )
            .scalars()
            .all()
        )
    return RecommendationsView(
        run=run,
        snapshot=SnapshotView(
            id=snap["id"],
            counts=json.loads(snap["counts"]),
            limitations=json.loads(snap["limitations"]),
            versions=json.loads(snap["versions"]),
            created_at=snap["created_at"],
        ),
        cards=[RecommendationCard.model_validate(json.loads(c)) for c in items],
    )


def cancel_run(run_id: str) -> RunView:
    with tx() as s:
        row = s.execute(text("SELECT status, job_id FROM matching_runs WHERE id=:id"), {"id": run_id}).first()
    if row is None:
        raise MatchingError("not_found", "匹配运行不存在", 404)
    if row.status not in ACTIVE:
        raise MatchingError("not_active", "该运行已结束")
    job = jobs.cancel_job(row.job_id) if row.job_id else None
    if job is None or job.status == "cancelled":
        with tx() as s:
            s.execute(
                text(
                    """UPDATE matching_runs SET status='cancelled', finished_at=:n, updated_at=:n
                       WHERE id=:id AND status IN ('queued','running')"""
                ),
                {"n": utcnow_iso(), "id": run_id},
            )
    return get_run(run_id)


def suggest_competitors(cm_id: str, keywords: str | None) -> dict[str, Any]:
    """按产品类目（及可选关键词）在本站点搜同类在售商品，作为竞品候选。每次 1 额度，不缓存。"""
    from tk_workspace.integrations.fastmoss import catalog

    with tx() as s:
        cm = (
            s.execute(
                text(
                    """SELECT cm.id, cm.product_version_id, m.data_status, m.fastmoss_region, m.name_zh,
                              (SELECT product_version_id FROM criteria_versions cv
                                WHERE cv.campaign_market_id = cm.id AND cv.status = 'draft') AS draft_pv
                       FROM campaign_markets cm JOIN markets m ON m.market_code = cm.market_code
                       WHERE cm.id = :id"""
                ),
                {"id": cm_id},
            )
            .mappings()
            .first()
        )
        if cm is None:
            raise MatchingError("not_found", "任务站点不存在", 404)
        if cm["data_status"] == "manual_import_only" or not cm["fastmoss_region"]:
            raise MatchingError("manual_import_only", f"{cm['name_zh']}没有 FastMoss 数据，不能推荐竞品")
        facts = s.execute(
            text("SELECT facts FROM product_versions WHERE id = :id"),
            {"id": cm["draft_pv"] or cm["product_version_id"]},
        ).scalar_one()
    category = json.loads(facts).get("category")
    kw = (keywords or "").strip() or None
    if not category and not kw:
        raise MatchingError(
            "need_category_or_keywords", "产品没有设置 TikTok 商品类目，请先设置类目或填写竞品关键词", 422
        )
    if provider.transport_name() == "mcp":
        from tk_workspace.modules.settings.service import get_fastmoss_key

        if not get_fastmoss_key():
            raise MatchingError("fastmoss_not_configured", "还没有填写 FastMoss API Key，请到设置页填写")
    path = None
    if category:
        path = [category[k] for k in ("l1_id", "l2_id", "l3_id") if category.get(k)]
    params = catalog.ProductSearchParams(
        filter=catalog.ProductSearchFilter(region=cm["fastmoss_region"], category_path=path),
        keywords=kw,
        orderby=[catalog.ProductOrderBy(field="day28_units_sold")],
    )
    out = provider.call_direct("product.search", params)
    if out.status == "unauthorized":
        raise MatchingError("fastmoss_unauthorized", "FastMoss 拒绝了 API Key，请到设置页检查", 502)
    if out.status == "insufficient_credits":
        raise MatchingError("insufficient_credits", "FastMoss 账户额度不足", 402)
    if out.status not in ("succeeded", "empty"):
        raise MatchingError("fastmoss_failed", f"竞品搜索失败：{out.message or out.status}", 502)
    return {
        "items": out.records,
        "credits_used": out.credits,
        "category_path": category.get("path") if category else None,
    }
