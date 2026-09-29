"""creator.search → FastMoss MCP `creator_search`：参数模型、条件编译、结果规范化。

- 只保存字段白名单内的规范化记录，不保存原始响应（FastMoss 条款要求数据保密）。
- 数字统一转成十进制字符串；取不到的字段为 None（未知），绝不填 0。
- 返回结构没有 outputSchema，按字段名在各层字典中查找；结构变化时字段变成未知，而不是报错或当成 0。
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
PAGE_SIZE = 10  # 每次扣费与条数无关（实测），固定取最大值


class FollowerRange(BaseModel):
    min: int | None = None
    max: int | None = None


class SearchFilter(BaseModel):
    region: str
    follower_range: FollowerRange | None = None
    creator_type: Literal[1, 2] | None = None
    is_ecommerce_creator: bool | None = None
    follower_age_type: Literal[1, 2, 3] | None = None
    # 带货商品类目（M2.1）：含义是“带过该类目商品”，不等于主营方向
    product_category_l1_id: int | None = None
    product_category_l2_id: int | None = None
    product_category_l3_id: int | None = None
    uid: str | None = Field(None, max_length=40)  # 按 uid 精确查询（补全竞品达人数据）


class OrderBy(BaseModel):
    field: Literal["follower_count", "day28_units_sold", "day28_gmv", "video_avg_play_count"]
    order: Literal["asc", "desc"] = "desc"


class CreatorSearchParams(BaseModel):
    filter: SearchFilter
    keywords: str | None = Field(None, max_length=100)
    orderby: list[OrderBy] | None = None
    page: int = Field(1, ge=1, le=500)
    pagesize: int = Field(PAGE_SIZE, ge=1, le=10)


_CREATOR_TYPE = {"personal": 1, "shop": 2}
_AGE = {"18-24": 1, "25-34": 2, "35+": 3}
_AGE_BACK = {1: "18-24", 2: "25-34", 3: "35+", "1": "18-24", "2": "25-34", "3": "35+"}


def compile_filter(region: str, hard_criteria: list[dict[str, Any]]) -> SearchFilter:
    """只把“硬条件 + FastMoss 能直接筛选”的字段编进搜索参数；软条件不能在源头排除候选。"""
    f = SearchFilter(region=region)
    for c in hard_criteria:
        key, op, exp = c["field_key"], c["operator"], c["expected"]
        if key == "follower_count":
            if op == "between":
                lo, hi = exp.get("min"), exp.get("max")
            elif op == "gte":
                lo, hi = exp, None
            else:
                lo, hi = None, exp
            f.follower_range = FollowerRange(
                min=int(Decimal(lo)) if lo is not None else None,
                max=int(Decimal(hi)) if hi is not None else None,
            )
        elif key == "creator_type":
            f.creator_type = _CREATOR_TYPE[exp]  # type: ignore[assignment]
        elif key == "is_ecommerce_creator":
            f.is_ecommerce_creator = bool(exp)
        elif key == "follower_age":
            f.follower_age_type = _AGE[exp]  # type: ignore[assignment]
    return f


# ---------------- 结果规范化 ----------------

_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "follower_count": ("follower_count", "followers", "fans_count"),
    "day28_follower_count": ("day28_follower_count",),
    "day28_gmv": ("day28_gmv",),
    "day28_units_sold": ("day28_units_sold", "day28_sold_count"),
    "total_units_sold": ("total_units_sold",),
    "video_units_sold": ("video_units_sold",),
    "video_count": ("video_count", "video_count_total"),
    "video_avg_play_count": ("video_avg_play_count", "avg_play_count"),
    "video_avg_digg_count": ("video_avg_digg_count",),
    "engagement_rate": ("engagement_rate_percent", "engagement_rate", "interaction_rate"),
}
_TEXT_ALIASES = {
    "provider_uid": ("uid", "creator_uid", "user_id"),
    "unique_id": ("unique_id", "handle", "username"),
    "nickname": ("nickname", "nick_name"),  # 不能用 name：类目对象也有 name
    "region": ("region", "country", "country_code"),
    "currency": ("currency", "currency_code"),
    "profile_text": ("signature", "bio", "description"),
}


def _walk(obj: Any, depth: int = 0):
    """深度优先遍历字典，产出 (key, value)。浅层优先，便于同名字段取最外层。"""
    if depth > 3 or not isinstance(obj, dict):
        return
    nested = []
    for k, v in obj.items():
        if isinstance(v, dict):
            nested.append(v)
        else:
            yield k, v
    for v in nested:
        yield from _walk(v, depth + 1)


def _find(rec: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    wanted = {a.lower() for a in aliases}
    for k, v in _walk(rec):
        if isinstance(k, str) and k.lower() in wanted and v not in (None, ""):
            return v
    return None


def to_decimal(v: Any) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if not v:
            return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    d = d.normalize()
    return format(d, "f")


def _bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v in (0, 1, "0", "1"):
        return str(v) == "1"
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


def _creator_type(v: Any) -> str | None:
    if v in (1, "1"):
        return "personal"
    if v in (2, "2"):
        return "shop"
    return None


def normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: None for k in _TEXT_ALIASES}
    for k, aliases in _TEXT_ALIASES.items():
        v = _find(rec, aliases)
        out[k] = str(v)[:300] if v is not None else None
    if out["currency"]:
        out["currency"] = out["currency"].upper()[:3]
    out["metrics"] = {}
    for key, aliases in _METRIC_ALIASES.items():
        raw = _find(rec, aliases)
        out["metrics"][key] = {
            "value": to_decimal(raw),
            "raw_type": type(raw).__name__ if raw is not None else None,
        }
    out["flags"] = {
        "is_ecommerce_creator": _bool(_find(rec, ("is_ecommerce_creator", "is_ecommerce"))),
        "has_email": _bool(_find(rec, ("has_email",))),
        "creator_type": _creator_type(_find(rec, ("creator_type", "creator_type_code"))),
    }
    age = _find(rec, ("follower_age_type", "top_age", "main_age"))
    top_age = _AGE_BACK.get(age) if age is not None else _top_age_bucket(_find_list(rec, "age_distribution"))
    out["audience"] = {"top_age": top_age}
    out["categories"] = _categories(rec)
    return out


def _find_list(rec: dict[str, Any], key: str) -> list[Any]:
    """在各层字典中找名为 key 的列表（_walk 只产出非字典值，列表也在其中）。"""
    for k, v in _walk(rec):
        if k == key and isinstance(v, list):
            return v
    return []


# 实测 audience_summary.age_distribution 的分段：18-24 / 25-34 / 35-44 / 45-54 / 55+，
# 搜索参数 follower_age_type 只有 18-24 / 25-34 / 35+ 三档，这里先并档再取占比最大的一档。
_AGE_BUCKET = {"18-24": "18-24", "25-34": "25-34", "35-44": "35+", "45-54": "35+", "55+": "35+"}


def _top_age_bucket(dist: list[Any]) -> str | None:
    sums: dict[str, Decimal] = {}
    for item in dist:
        if not isinstance(item, dict):
            continue
        bucket = _AGE_BUCKET.get(str(item.get("age_range") or "").strip())
        pct = to_decimal(item.get("percentage"))
        if bucket is None or pct is None:
            continue
        sums[bucket] = sums.get(bucket, Decimal(0)) + Decimal(pct)
    if not sums or max(sums.values()) <= 0:
        return None
    return max(sums, key=lambda b: sums[b])


def _categories(rec: dict[str, Any]) -> str | None:
    """账号类目 + 近期带货类目，给 AI 判断产品相关性用（只是类目名，不是个人信息）。"""
    names: list[str] = []
    creator = rec.get("creator") if isinstance(rec.get("creator"), dict) else rec
    cat = creator.get("category") if isinstance(creator, dict) else None
    if isinstance(cat, dict) and cat.get("name"):
        names.append(f"账号类目：{str(cat['name'])[:60]}")
    trend = [
        str(x["name"])[:40]
        for x in _find_list(rec, "trend_categories")
        if isinstance(x, dict) and x.get("name")
    ][:5]
    if trend:
        names.append("近期带货类目：" + "、".join(trend))
    return "；".join(names) or None


def extract_list(data: Any) -> tuple[list[dict[str, Any]], int | None]:
    """从响应中取出记录列表和 total。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)], None
    if not isinstance(data, dict):
        return [], None
    total = data.get("total")
    for key in ("list", "items", "data", "records", "creators", "result"):
        v = data.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)], _int(total)
        if isinstance(v, dict):
            inner, t = extract_list(v)
            if inner:
                return inner, _int(total) if total is not None else t
    return [], _int(total)


def normalize_search_result(data: Any, _params: Any = None) -> tuple[list[dict[str, Any]], int | None]:
    raw, total = extract_list(data)
    return [normalize_record(r) for r in raw], total


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
