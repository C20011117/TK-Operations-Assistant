"""找人条件的受限 DSL。

每个条件 = 字段（白名单）+ 操作符（按字段限定）+ 期望值（按类型校验）+ 硬/软 + 未知时如何处理。
字段表同时记录 FastMoss 的真实能力，界面据此提示“能在搜索时筛选”还是“只能对结果判断”或“数据源没有、需人工核实”，
不能让用户误以为 FastMoss 支持并不存在的筛选。

能力来源：2026-09-29 FastMoss MCP `creator_search` 的 inputSchema 与返回字段。
"""

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

Support = Literal["search_filter", "result_field", "not_provided"]
Operator = Literal["between", "gte", "lte", "eq", "in"]
ValueType = Literal["int_range", "int", "money", "percent", "enum", "enum_multi", "bool"]

SUPPORT_LABELS = {
    "search_filter": "FastMoss 搜索时直接筛选",
    "result_field": "FastMoss 返回结果后按字段判断",
    "not_provided": "FastMoss 没有此数据，需人工核实（在核实前一律为“未知”）",
}


class FieldOption(BaseModel):
    value: str
    label: str


class FieldSpec(BaseModel):
    key: str
    label: str
    description: str
    value_type: ValueType
    operators: list[Operator]
    support: Support
    support_label: str
    unit: str | None = None
    options: list[FieldOption] = Field(default_factory=list)
    data_quality_note: str | None = None


def _f(**kw) -> FieldSpec:
    return FieldSpec(support_label=SUPPORT_LABELS[kw["support"]], **kw)


FIELDS: dict[str, FieldSpec] = {
    f.key: f
    for f in [
        _f(
            key="follower_count",
            label="粉丝数",
            description="达人账号粉丝数",
            value_type="int_range",
            operators=["between", "gte", "lte"],
            support="search_filter",
            unit="人",
        ),
        _f(
            key="creator_type",
            label="账号类型",
            description="个人达人或店铺号",
            value_type="enum",
            operators=["eq"],
            support="search_filter",
            options=[
                FieldOption(value="personal", label="个人达人"),
                FieldOption(value="shop", label="店铺号"),
            ],
        ),
        _f(
            key="is_ecommerce_creator",
            label="是否带货达人",
            description="FastMoss 标记的带货达人；不等于已证明在该站点具备带货资格",
            value_type="bool",
            operators=["eq"],
            support="search_filter",
        ),
        _f(
            key="follower_age",
            label="粉丝主要年龄段",
            description="粉丝年龄分布中的主要区间",
            value_type="enum",
            operators=["eq"],
            support="search_filter",
            options=[
                FieldOption(value="18-24", label="18–24 岁"),
                FieldOption(value="25-34", label="25–34 岁"),
                FieldOption(value="35+", label="35 岁以上"),
            ],
        ),
        _f(
            key="day28_gmv",
            label="近 28 天带货 GMV",
            description="按站点结算币种比较",
            value_type="money",
            operators=["gte", "lte"],
            support="result_field",
            data_quality_note="部分站点返回的币种为空；币种未知时该条件判为“未知”",
        ),
        _f(
            key="day28_units_sold",
            label="近 28 天销量",
            description="近 28 天带货件数",
            value_type="int",
            operators=["gte", "lte"],
            support="result_field",
            unit="件",
            data_quality_note="销量字段口径不一，异常值会标记为“异常”",
        ),
        _f(
            key="video_avg_play_count",
            label="视频平均播放",
            description="近期视频平均播放量",
            value_type="int",
            operators=["gte"],
            support="result_field",
            unit="次",
        ),
        _f(
            key="engagement_rate",
            label="互动率",
            description="视频互动率",
            value_type="percent",
            operators=["gte"],
            support="result_field",
            unit="%",
            data_quality_note="数据源返回 0 时多为缺失，按“未知”处理",
        ),
        _f(
            key="has_email",
            label="有联系邮箱",
            description="只知道是否有邮箱，不含邮箱地址",
            value_type="bool",
            operators=["eq"],
            support="result_field",
        ),
        _f(
            key="content_language",
            label="内容语言",
            description="达人视频使用的语言；与销售站点、受众所在地分开判断",
            value_type="enum_multi",
            operators=["in"],
            support="not_provided",
            options=[
                FieldOption(value=v, label=lab)
                for v, lab in [
                    ("en", "英语"),
                    ("de", "德语"),
                    ("fr", "法语"),
                    ("it", "意大利语"),
                    ("es", "西班牙语"),
                    ("nl", "荷兰语"),
                    ("pl", "波兰语"),
                    ("pt", "葡萄牙语"),
                ]
            ],
        ),
    ]
}


class Criterion(BaseModel):
    criterion_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    field_key: str
    operator: Operator
    expected: Any
    hardness: Literal["hard", "soft"]
    unknown_policy: Literal["keep", "exclude"] = Field(
        "keep", description="数据未知时：keep 保留为“待核实”，exclude 排除（仅硬条件可选）"
    )
    provenance: Literal["user", "model_suggested"] = "user"
    note: str = Field("", max_length=200)


class SearchInput(BaseModel):
    keywords: list[str] = Field(
        default_factory=list, max_length=5, description="搜索关键词（产品、品类、内容主题）"
    )


class CriteriaIssue(BaseModel):
    criterion_id: str | None
    message: str


def _num(v: Any, kind: str) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise ValueError
    try:
        d = Decimal(str(v))
    except InvalidOperation as e:
        raise ValueError from e
    if d < 0 or not d.is_finite():
        raise ValueError
    if kind == "int" and d != d.to_integral_value():
        raise ValueError
    if kind == "percent" and d > 100:
        raise ValueError
    return d


def normalize(
    criteria: list[Criterion], market_languages: list[str]
) -> tuple[list[Criterion], list[CriteriaIssue]]:
    """校验并规范化期望值（数字统一为字符串），返回 (规范化后的条件, 问题列表)。"""
    issues: list[CriteriaIssue] = []
    out: list[Criterion] = []
    seen: set[tuple[str, str]] = set()
    for c in criteria:
        spec = FIELDS.get(c.field_key)
        if not spec:
            issues.append(
                CriteriaIssue(criterion_id=c.criterion_id, message=f"不支持的条件字段：{c.field_key}")
            )
            continue
        if c.operator not in spec.operators:
            issues.append(
                CriteriaIssue(criterion_id=c.criterion_id, message=f"“{spec.label}”不支持该比较方式")
            )
            continue
        if (c.field_key, c.operator) in seen:
            issues.append(CriteriaIssue(criterion_id=c.criterion_id, message=f"“{spec.label}”重复设置"))
            continue
        seen.add((c.field_key, c.operator))
        if c.hardness == "soft" and c.unknown_policy == "exclude":
            issues.append(
                CriteriaIssue(
                    criterion_id=c.criterion_id, message=f"“{spec.label}”是软条件，未知时不能直接排除"
                )
            )
            continue
        try:
            expected = _normalize_value(spec, c.operator, c.expected)
        except ValueError:
            issues.append(CriteriaIssue(criterion_id=c.criterion_id, message=f"“{spec.label}”的取值不正确"))
            continue
        if spec.key == "content_language":
            missing = [lang for lang in expected if lang not in market_languages]
            if missing:
                issues.append(
                    CriteriaIssue(
                        criterion_id=c.criterion_id,
                        message=f"内容语言 {', '.join(missing)} 不是该站点的常用语言（{', '.join(market_languages)}），请确认",
                    )
                )
                continue
        out.append(c.model_copy(update={"expected": expected}))
    return out, issues


def _normalize_value(spec: FieldSpec, op: str, v: Any) -> Any:
    t = spec.value_type
    if t == "int_range":
        if op == "between":
            if not isinstance(v, dict):
                raise ValueError
            lo = _num(v.get("min"), "int") if v.get("min") not in (None, "") else None
            hi = _num(v.get("max"), "int") if v.get("max") not in (None, "") else None
            if lo is None and hi is None:
                raise ValueError
            if lo is not None and hi is not None and lo > hi:
                raise ValueError
            return {"min": None if lo is None else str(lo), "max": None if hi is None else str(hi)}
        return str(_num(v, "int"))
    if t in ("int", "money", "percent"):
        d = _num(v, "int" if t == "int" else t)
        if t == "money" and d.as_tuple().exponent < -2:
            raise ValueError
        return str(d)
    if t == "bool":
        if not isinstance(v, bool):
            raise ValueError
        return v
    allowed = {o.value for o in spec.options}
    if t == "enum":
        if v not in allowed:
            raise ValueError
        return v
    if t == "enum_multi":
        if not isinstance(v, list) or not v or any(x not in allowed for x in v):
            raise ValueError
        return sorted(set(v))
    raise ValueError


def normalize_search(search: SearchInput) -> tuple[SearchInput, list[CriteriaIssue]]:
    words, issues = [], []
    for w in search.keywords:
        w = w.strip()
        if not w:
            continue
        if len(w) > 50:
            issues.append(CriteriaIssue(criterion_id=None, message=f"关键词过长：{w[:20]}…"))
            continue
        if w.lower() not in (x.lower() for x in words):
            words.append(w)
    return SearchInput(keywords=words), issues
