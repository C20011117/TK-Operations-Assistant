"""产品档案：版本化的产品事实与各站点价格条款。

规则：
- 修改产品只会写入草稿版本；确认后生成新的“当前版本”，旧版本变为 superseded，内容永不修改。
- 每个产品最多一个草稿、一个当前版本（数据库部分唯一索引兜底）。
- 价格币种来自站点目录，不能手填；价格未知必须明确标“未知”，不能用 0 代替。
"""

import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tk_workspace.modules.knowledge.schemas import (
    DraftIn,
    MarketTerm,
    ProductCreate,
    ProductDetail,
    ProductFacts,
    ProductSummary,
    ProductUpdate,
    ProductVersion,
    VersionSummary,
)
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso


class ProductError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _not_found() -> ProductError:
    return ProductError("not_found", "产品不存在", 404)


def _hash(draft: DraftIn) -> str:
    payload = {
        # 未设置类目时不写入该键，保证加字段前后同样内容的哈希不变
        "facts": draft.facts.model_dump(mode="json", exclude_none=True),
        "terms": sorted(
            (t.model_dump(mode="json") for t in draft.market_terms), key=lambda t: t["market_code"]
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _market_currencies(s: Session) -> dict[str, str]:
    rows = s.execute(text("SELECT market_code, settlement_currency FROM markets")).all()
    return {r[0]: r[1] for r in rows}


# ---------- 读取 ----------


def _load_version(s: Session, version_id: str) -> ProductVersion:
    v = s.execute(text("SELECT * FROM product_versions WHERE id = :id"), {"id": version_id}).mappings().one()
    terms = (
        s.execute(
            text("SELECT * FROM product_market_terms WHERE product_version_id = :id ORDER BY market_code"),
            {"id": version_id},
        )
        .mappings()
        .all()
    )
    return ProductVersion(
        id=v["id"],
        version_no=v["version_no"],
        status=v["status"],
        facts=ProductFacts.model_validate_json(v["facts"]),
        market_terms=[
            MarketTerm(
                market_code=t["market_code"],
                price_status=t["price_status"],
                price_amount=t["price_amount"],
                price_currency=t["price_currency"],
                sample_policy=t["sample_policy"],
                commission_min_pct=t["commission_min_pct"],
                commission_max_pct=t["commission_max_pct"],
                quote_valid_until=t["quote_valid_until"],
                notes=t["notes"],
            )
            for t in terms
        ],
        content_hash=v["content_hash"],
        created_at=v["created_at"],
        confirmed_at=v["confirmed_at"],
    )


def get_version(version_id: str) -> ProductVersion:
    with tx() as s:
        exists = s.execute(text("SELECT 1 FROM product_versions WHERE id = :id"), {"id": version_id}).first()
        if not exists:
            raise ProductError("not_found", "产品版本不存在", 404)
        return _load_version(s, version_id)


def _summary_row(s: Session, p) -> dict:
    cur_no, codes = None, []
    if p["current_version_id"]:
        cur_no = s.execute(
            text("SELECT version_no FROM product_versions WHERE id = :id"), {"id": p["current_version_id"]}
        ).scalar_one()
        codes = list(
            s.execute(
                text(
                    "SELECT market_code FROM product_market_terms WHERE product_version_id = :id ORDER BY market_code"
                ),
                {"id": p["current_version_id"]},
            ).scalars()
        )
    has_draft = bool(
        s.execute(
            text("SELECT 1 FROM product_versions WHERE product_id = :id AND status = 'draft'"),
            {"id": p["id"]},
        ).first()
    )
    return {
        "id": p["id"],
        "sku": p["sku"],
        "name": p["name"],
        "status": p["status"],
        "current_version_no": cur_no,
        "has_draft": has_draft,
        "market_codes": codes,
        "updated_at": p["updated_at"],
    }


def list_products(include_archived: bool = False) -> list[ProductSummary]:
    sql = (
        "SELECT * FROM products ORDER BY updated_at DESC"
        if include_archived
        else "SELECT * FROM products WHERE status = 'active' ORDER BY updated_at DESC"
    )
    with tx() as s:
        return [ProductSummary(**_summary_row(s, p)) for p in s.execute(text(sql)).mappings().all()]


def get_product(product_id: str) -> ProductDetail:
    with tx() as s:
        return _detail(s, product_id)


def _detail(s: Session, product_id: str) -> ProductDetail:
    p = s.execute(text("SELECT * FROM products WHERE id = :id"), {"id": product_id}).mappings().first()
    if not p:
        raise _not_found()
    versions = (
        s.execute(
            text(
                "SELECT id, version_no, status, created_at, confirmed_at FROM product_versions"
                " WHERE product_id = :id ORDER BY version_no DESC"
            ),
            {"id": product_id},
        )
        .mappings()
        .all()
    )
    draft_id = next((v["id"] for v in versions if v["status"] == "draft"), None)
    return ProductDetail(
        **_summary_row(s, p),
        current=_load_version(s, p["current_version_id"]) if p["current_version_id"] else None,
        draft=_load_version(s, draft_id) if draft_id else None,
        versions=[VersionSummary(**v) for v in versions],
    )


# ---------- 写入 ----------


def create_product(body: ProductCreate) -> ProductDetail:
    now = utcnow_iso()
    pid = str(uuid.uuid4())
    try:
        with tx() as s:
            s.execute(
                text(
                    "INSERT INTO products (id, sku, name, status, created_at, updated_at)"
                    " VALUES (:id, :sku, :name, 'active', :now, :now)"
                ),
                {"id": pid, "sku": body.sku, "name": body.name, "now": now},
            )
            _write_draft(s, pid, DraftIn(facts=ProductFacts()), now)
            return _detail(s, pid)
    except IntegrityError as e:
        raise ProductError("sku_exists", f"SKU “{body.sku}” 已存在") from e


def update_product(product_id: str, body: ProductUpdate) -> ProductDetail:
    """名称与 SKU 是标识信息，不属于版本化的产品事实。"""
    try:
        with tx() as s:
            n = s.execute(
                text("UPDATE products SET sku = :sku, name = :name, updated_at = :now WHERE id = :id"),
                {"id": product_id, "sku": body.sku, "name": body.name, "now": utcnow_iso()},
            ).rowcount
            if not n:
                raise _not_found()
            return _detail(s, product_id)
    except IntegrityError as e:
        raise ProductError("sku_exists", f"SKU “{body.sku}” 已存在") from e


def _write_draft(s: Session, product_id: str, body: DraftIn, now: str) -> str:
    currencies = _market_currencies(s)
    unknown = [t.market_code for t in body.market_terms if t.market_code not in currencies]
    if unknown:
        raise ProductError("unknown_market", f"未知站点：{', '.join(unknown)}", 422)
    draft_id = s.execute(
        text("SELECT id FROM product_versions WHERE product_id = :p AND status = 'draft'"), {"p": product_id}
    ).scalar()
    facts_json = body.facts.model_dump_json()
    if draft_id:
        s.execute(
            text(
                "UPDATE product_versions SET facts = :f, content_hash = :h, updated_at = :now WHERE id = :id"
            ),
            {"f": facts_json, "h": _hash(body), "now": now, "id": draft_id},
        )
        s.execute(text("DELETE FROM product_market_terms WHERE product_version_id = :id"), {"id": draft_id})
    else:
        draft_id = str(uuid.uuid4())
        next_no = s.execute(
            text("SELECT COALESCE(MAX(version_no), 0) + 1 FROM product_versions WHERE product_id = :p"),
            {"p": product_id},
        ).scalar_one()
        s.execute(
            text(
                "INSERT INTO product_versions (id, product_id, version_no, status, facts, content_hash, created_at, updated_at)"
                " VALUES (:id, :p, :no, 'draft', :f, :h, :now, :now)"
            ),
            {"id": draft_id, "p": product_id, "no": next_no, "f": facts_json, "h": _hash(body), "now": now},
        )
    for t in body.market_terms:
        s.execute(
            text(
                """INSERT INTO product_market_terms
                   (id, product_version_id, market_code, price_status, price_amount, price_currency, sample_policy,
                    commission_min_pct, commission_max_pct, quote_valid_until, notes)
                   VALUES (:id, :v, :m, :ps, :pa, :cur, :sp, :cmin, :cmax, :qv, :notes)"""
            ),
            {
                "id": str(uuid.uuid4()),
                "v": draft_id,
                "m": t.market_code,
                "ps": t.price_status,
                "pa": t.price_amount,
                "cur": currencies[t.market_code],
                "sp": t.sample_policy,
                "cmin": t.commission_min_pct,
                "cmax": t.commission_max_pct,
                "qv": t.quote_valid_until.isoformat() if t.quote_valid_until else None,
                "notes": t.notes,
            },
        )
    s.execute(text("UPDATE products SET updated_at = :now WHERE id = :id"), {"now": now, "id": product_id})
    return draft_id


def _require_active(s: Session, product_id: str) -> None:
    st = s.execute(text("SELECT status FROM products WHERE id = :id"), {"id": product_id}).scalar()
    if st is None:
        raise _not_found()
    if st != "active":
        raise ProductError("archived", "产品已归档，不能修改")


def save_draft(product_id: str, body: DraftIn) -> ProductDetail:
    with tx() as s:
        _require_active(s, product_id)
        _write_draft(s, product_id, body, utcnow_iso())
        return _detail(s, product_id)


def discard_draft(product_id: str) -> ProductDetail:
    with tx() as s:
        _require_active(s, product_id)
        s.execute(
            text("DELETE FROM product_versions WHERE product_id = :p AND status = 'draft'"), {"p": product_id}
        )
        return _detail(s, product_id)


def draft_problems(v: ProductVersion) -> list[str]:
    problems = []
    if not v.facts.summary:
        problems.append("请填写产品简介")
    if not v.facts.selling_points:
        problems.append("至少填写一个卖点")
    if not v.market_terms:
        problems.append("至少填写一个站点的价格条款")
    return problems


def confirm_draft(product_id: str) -> ProductDetail:
    now = utcnow_iso()
    with tx() as s:
        _require_active(s, product_id)
        draft_id = s.execute(
            text("SELECT id FROM product_versions WHERE product_id = :p AND status = 'draft'"),
            {"p": product_id},
        ).scalar()
        if not draft_id:
            raise ProductError("no_draft", "没有待确认的草稿")
        draft = _load_version(s, draft_id)
        problems = draft_problems(draft)
        if problems:
            raise ProductError(
                "draft_incomplete", "草稿还不完整：" + "；".join(problems), 422, {"problems": problems}
            )
        cur_id = s.execute(
            text("SELECT current_version_id FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        if cur_id:
            cur_hash = s.execute(
                text("SELECT content_hash FROM product_versions WHERE id = :id"), {"id": cur_id}
            ).scalar_one()
            if cur_hash == draft.content_hash:
                raise ProductError("no_changes", "草稿与当前版本完全相同，无需确认")
            s.execute(
                text("UPDATE product_versions SET status = 'superseded', updated_at = :now WHERE id = :id"),
                {"now": now, "id": cur_id},
            )
        s.execute(
            text(
                "UPDATE product_versions SET status = 'confirmed', confirmed_at = :now, updated_at = :now WHERE id = :id"
            ),
            {"now": now, "id": draft_id},
        )
        s.execute(
            text("UPDATE products SET current_version_id = :v, updated_at = :now WHERE id = :id"),
            {"v": draft_id, "now": now, "id": product_id},
        )
        return _detail(s, product_id)


def start_draft_from_current(product_id: str) -> ProductDetail:
    """以当前版本为底稿开始修改（已有草稿则直接返回）。"""
    with tx() as s:
        _require_active(s, product_id)
        has_draft = s.execute(
            text("SELECT 1 FROM product_versions WHERE product_id = :p AND status = 'draft'"),
            {"p": product_id},
        ).first()
        cur_id = s.execute(
            text("SELECT current_version_id FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        if not has_draft:
            base = _load_version(s, cur_id) if cur_id else None
            body = (
                DraftIn(
                    facts=base.facts,
                    market_terms=[t.model_dump(exclude={"price_currency"}) for t in base.market_terms],
                )
                if base
                else DraftIn(facts=ProductFacts())
            )
            _write_draft(s, product_id, body, utcnow_iso())
        return _detail(s, product_id)


def set_archived(product_id: str, archived: bool) -> ProductDetail:
    try:
        with tx() as s:
            n = s.execute(
                text("UPDATE products SET status = :st, updated_at = :now WHERE id = :id"),
                {"st": "archived" if archived else "active", "now": utcnow_iso(), "id": product_id},
            ).rowcount
            if not n:
                raise _not_found()
            return _detail(s, product_id)
    except IntegrityError as e:
        raise ProductError("sku_exists", "已有同 SKU 的在用产品，不能恢复") from e


def suggest_categories(query: list[str]) -> list[dict]:
    """FastMoss 类目识别（不扣费）。未配置 Key 或调用失败时给出明确错误，不返回猜测结果。"""
    from tk_workspace.integrations.fastmoss.catalog import CategorySearchParams
    from tk_workspace.modules.matching import provider
    from tk_workspace.modules.settings.service import get_fastmoss_key

    if provider.transport_name() == "mcp" and not get_fastmoss_key():
        raise ProductError("fastmoss_not_configured", "请先在设置页填写 FastMoss API Key", 409)
    out = provider.call_direct("category.search", CategorySearchParams(query=query))
    if out.status in ("succeeded", "empty"):
        return out.records
    if out.status == "unauthorized":
        raise ProductError("fastmoss_unauthorized", "FastMoss 拒绝了 API Key，请到设置页检查", 502)
    raise ProductError("fastmoss_failed", f"类目识别失败：{out.message or out.status}", 502)
