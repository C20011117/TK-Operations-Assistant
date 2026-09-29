"""商品类目识别、竞品搜索、竞品达人（M2.1 按产品找达人）：参数模型与结果规范化。

结构依据 2026-09-29 实测（摄像头 · GB）：
- search_category_by_words → {params, result: {categories: [{category_id_level1/2/3, cn_name, cn_full_name, score,
  matched_query}], total}}，不扣费。
- product_search → {list: [{product: {product_id, title, category: {l1,l2,l3}, currency_code, price_display, ...},
  sales_summary: {last_28d_gmv, last_28d_units_sold, ...}, distribution_summary: {linked_creator_count, ...},
  shop}], total}，每次 1 额度。
- product_creator_analysis → {creator_summary, linked_creators: {list: [{creator: {creator_uid, creator_handle,
  creator_name, creator_category, follower_count, region}, product_contribution: {product_gmv, product_units_sold,
  ...}, creator_cumulative_performance, audience_summary}], total}}，每次 3 额度；不含近 28 天 GMV、互动率。

和 creator_search 一样：只保存白名单字段，不保存原始响应；取不到的值为 None，绝不填 0。
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from tk_workspace.integrations.fastmoss.creator_search import _METRIC_ALIASES, _top_age_bucket, to_decimal

# ---------------- 类目识别 ----------------


class CategorySearchParams(BaseModel):
    query: list[str] = Field(..., min_length=1, max_length=5)
    top_k: int = Field(5, ge=1, le=10)
    max_total_results: int = Field(15, ge=1, le=30)


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def _find_key(obj: Any, key: str, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    return None


def normalize_categories(data: Any, _params: Any = None) -> tuple[list[dict[str, Any]], int | None]:
    items = _find_key(data, "categories")
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict):
            continue
        l1, l2, l3 = (_int(c.get(f"category_id_level{n}")) for n in (1, 2, 3))
        if l1 is None:
            continue
        key = (l1, l2, l3)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "l1_id": l1,
                "l2_id": l2,
                "l3_id": l3,
                "name": str(c.get("cn_name") or "")[:100] or None,
                "path": str(c.get("cn_full_name") or c.get("cn_name") or "")[:300] or None,
                "score": to_decimal(c.get("score")),
                "matched_query": str(c.get("matched_query") or "")[:100] or None,
            }
        )
    out.sort(key=lambda x: -float(x["score"] or 0))
    return out, len(out)


# ---------------- 竞品搜索 ----------------


class ProductSearchFilter(BaseModel):
    region: str
    category_path: list[int] | None = Field(None, min_length=1, max_length=3)
    listing_status: Literal["on_sale", "off_shelf"] | None = None


class ProductOrderBy(BaseModel):
    field: Literal["day28_units_sold", "day28_gmv", "total_units_sold", "creator_count"] = "day28_units_sold"
    order: Literal["asc", "desc"] = "desc"


class ProductSearchParams(BaseModel):
    filter: ProductSearchFilter
    keywords: str | None = Field(None, max_length=100)
    orderby: list[ProductOrderBy] | None = None
    page: int = Field(1, ge=1, le=500)
    pagesize: int = Field(10, ge=1, le=10)


def normalize_products(data: Any, _params: Any = None) -> tuple[list[dict[str, Any]], int | None]:
    if not isinstance(data, dict):
        return [], None
    out = []
    for it in data.get("list") or []:
        if not isinstance(it, dict):
            continue
        p = it.get("product") or {}
        pid = str(p.get("product_id") or "").strip()
        if not pid.isdigit():
            continue
        cat = p.get("category") or {}
        path = " › ".join(
            str((cat.get(k) or {}).get("name")) for k in ("l1", "l2", "l3") if (cat.get(k) or {}).get("name")
        )
        ss, ds = it.get("sales_summary") or {}, it.get("distribution_summary") or {}
        out.append(
            {
                "product_id": pid,
                "title": str(p.get("title") or "")[:300] or None,
                "category_path": path or None,
                "currency": (str(p.get("currency_code") or "").upper()[:3]) or None,
                "price_display": str(p.get("price_display") or "")[:40] or None,
                "day28_units_sold": to_decimal(ss.get("last_28d_units_sold")),
                "day28_gmv": to_decimal(ss.get("last_28d_gmv")),
                "linked_creator_count": to_decimal(ds.get("linked_creator_count")),
                "shop_name": str((it.get("shop") or {}).get("shop_name") or "")[:100] or None,
            }
        )
    total = data.get("total")
    return out, int(total) if isinstance(total, int) else None


_PRODUCT_ID_RE = re.compile(r"(?:/product/|product_id=|^)(\d{15,21})(?:\D|$)")


def parse_product_ref(text: str) -> str | None:
    """从 TikTok 商品链接或纯编号中取出商品编号。"""
    t = (text or "").strip()
    m = _PRODUCT_ID_RE.search(t)
    return m.group(1) if m else None


# ---------------- 竞品达人 ----------------


class ProductCreatorsFilter(BaseModel):
    product_id: str = Field(..., pattern=r"^\d{15,21}$")


class ProductCreatorsOrderBy(BaseModel):
    field: Literal["product_gmv", "product_units_sold", "follower_count"] = "product_gmv"
    order: Literal["asc", "desc"] = "desc"


class ProductCreatorsParams(BaseModel):
    filter: ProductCreatorsFilter
    orderby: list[ProductCreatorsOrderBy] | None = None
    page: int = Field(1, ge=1, le=500)
    pagesize: int = Field(10, ge=1, le=10)


def normalize_linked_creators(data: Any, params: Any = None) -> tuple[list[dict[str, Any]], int | None]:
    """竞品达人 → 与 creator_search 相同形状的“部分”记录（partial=True，指标只有粉丝数）。"""
    lc = data.get("linked_creators") if isinstance(data, dict) else None
    items = lc.get("list") if isinstance(lc, dict) else lc if isinstance(lc, list) else []
    total = lc.get("total") if isinstance(lc, dict) else None
    product_id = getattr(getattr(params, "filter", None), "product_id", None)
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        cr = it.get("creator") or {}
        uid = str(cr.get("creator_uid") or "").strip() or None
        handle = str(cr.get("creator_handle") or "").strip().lstrip("@") or None
        if not uid and not handle:
            continue
        cat = cr.get("creator_category") or {}
        pc = it.get("product_contribution") or {}
        metrics = {k: {"value": None, "raw_type": None} for k in _METRIC_ALIASES}
        fc = cr.get("follower_count")
        metrics["follower_count"] = {
            "value": to_decimal(fc),
            "raw_type": type(fc).__name__ if fc is not None else None,
        }
        age = (it.get("audience_summary") or {}).get("age_distribution") or []
        out.append(
            {
                "provider_uid": uid,
                "unique_id": handle,
                "nickname": str(cr.get("creator_name") or "")[:300] or None,
                "region": (str(cr.get("region") or "").upper()[:2]) or None,
                "currency": None,
                "profile_text": None,
                "metrics": metrics,
                "flags": {"is_ecommerce_creator": None, "has_email": None, "creator_type": None},
                "audience": {"top_age": _top_age_bucket(age)},
                "categories": f"账号类目：{str(cat['name'])[:60]}" if cat.get("name") else None,
                "partial": True,
                "competitor_sales": [
                    {
                        "product_id": product_id,
                        "product_gmv": to_decimal(pc.get("product_gmv")),
                        "product_units_sold": to_decimal(pc.get("product_units_sold")),
                    }
                ],
            }
        )
    return out, int(total) if isinstance(total, int) else None
