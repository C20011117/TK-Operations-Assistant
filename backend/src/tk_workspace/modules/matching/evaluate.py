"""数据质量检查 + 三态条件判断（纯代码，不交给模型）。

观测状态：known 已知 | unknown 未知 | anomaly 数据异常 | inconsistent 字段互相矛盾。
只有 known 的观测能让条件判为 pass / fail；其他一律 unknown，并说明原因。
"""

from decimal import Decimal
from typing import Any

from tk_workspace.modules.campaigns.criteria import FIELDS

# 推荐卡上展示的指标（顺序即展示顺序）
DISPLAY_METRICS = (
    "follower_count",
    "day28_gmv",
    "day28_units_sold",
    "video_avg_play_count",
    "engagement_rate",
)
UNIT_PRICE_FALLBACK_LIMIT = Decimal("2000")  # 不知道产品价格时，折算单价超过它视为异常
UNIT_PRICE_MULTIPLIER = Decimal("20")  # 知道产品价格时，折算单价超过售价的 20 倍视为异常
UNIT_PRICE_MIN_LIMIT = Decimal("500")


def _d(v: str | None) -> Decimal | None:
    return Decimal(v) if v is not None else None


def _fmt(v: Decimal) -> str:
    return f"{v:,.0f}" if v >= 100 else f"{v:,.2f}"


def build_observations(
    record: dict[str, Any],
    reporting_currency: str,
    product_price: str | None,
    product_price_currency: str | None,
) -> dict[str, dict[str, Any]]:
    """规范化记录 → 观测。每条观测：{value, state, currency?, note}。"""
    m = record.get("metrics") or {}
    flags = record.get("flags") or {}
    obs: dict[str, dict[str, Any]] = {}

    def put(
        key: str, value: Any, state: str | None = None, note: str = "", currency: str | None = None
    ) -> None:
        if state is None:
            state = "known" if value is not None else "unknown"
        if state == "unknown" and not note:
            note = "数据源未返回"
        o: dict[str, Any] = {"value": value, "state": state, "note": note}
        if key == "day28_gmv":
            o["currency"] = currency
        obs[key] = o

    for key in (
        "follower_count",
        "day28_units_sold",
        "video_avg_play_count",
        "total_units_sold",
        "video_units_sold",
    ):
        put(key, (m.get(key) or {}).get("value"))

    er = (m.get("engagement_rate") or {}).get("value")
    if er is not None and Decimal(er) == 0:
        put("engagement_rate", None, "unknown", "数据源返回 0，多为缺失，按未知处理")
    else:
        put("engagement_rate", er)

    currency = record.get("currency")
    gmv = (m.get("day28_gmv") or {}).get("value")
    put(
        "day28_gmv",
        gmv,
        note="" if currency or gmv is None else "币种未知",
        currency=currency,
    )

    # 合理性检查：GMV ÷ 销量折算单价
    g, u = _d(gmv), _d(obs["day28_units_sold"]["value"])
    if g is not None and u is not None:
        limit = UNIT_PRICE_FALLBACK_LIMIT
        if product_price and product_price_currency and product_price_currency == currency:
            limit = max(Decimal(product_price) * UNIT_PRICE_MULTIPLIER, UNIT_PRICE_MIN_LIMIT)
        if u == 0 and g > 0:
            note = "有 GMV 但销量为 0，数据异常，待核实"
            obs["day28_gmv"].update(state="anomaly", note=note)
            obs["day28_units_sold"].update(state="anomaly", note=note)
        elif u > 0 and g / u > limit:
            note = f"GMV ÷ 销量 ≈ 每件 {_fmt(g / u)} {currency or '（币种未知）'}，超出合理范围，数据异常，待核实"
            obs["day28_gmv"].update(state="anomaly", note=note)
            obs["day28_units_sold"].update(state="anomaly", note=note)

    # 一致性检查：总销量为 0 但视频销量 > 0
    t, v = _d(obs["total_units_sold"]["value"]), _d(obs["video_units_sold"]["value"])
    if t is not None and v is not None and t == 0 and v > 0:
        note = "总销量为 0 但视频销量大于 0，字段互相矛盾"
        obs["total_units_sold"].update(state="inconsistent", note=note)
        obs["video_units_sold"].update(state="inconsistent", note=note)

    by_filter = record.get("filter_guaranteed") or {}
    if flags.get("is_ecommerce_creator") is None and "is_ecommerce_creator" in by_filter:
        put(
            "is_ecommerce_creator",
            by_filter["is_ecommerce_creator"],
            note="结果未返回此字段，由 FastMoss 搜索条件保证",
        )
    else:
        put("is_ecommerce_creator", flags.get("is_ecommerce_creator"))
    put("has_email", flags.get("has_email"))
    put("creator_type", flags.get("creator_type"))
    put("follower_age", (record.get("audience") or {}).get("top_age"))
    if record.get("categories"):
        put("categories", record["categories"])
    put("content_language", None, "unknown", "FastMoss 不提供内容语言，需人工查看视频确认")
    put(
        "selling_eligibility",
        None,
        "unknown",
        "FastMoss 的地区是达人所在地，不能证明其具备该站点带货资格",
    )
    return obs


def _cmp(op: str, actual: Decimal, exp: Any) -> bool:
    if op == "between":
        lo, hi = exp.get("min"), exp.get("max")
        return (lo is None or actual >= Decimal(lo)) and (hi is None or actual <= Decimal(hi))
    if op == "gte":
        return actual >= Decimal(exp)
    if op == "lte":
        return actual <= Decimal(exp)
    return actual == Decimal(exp)


def describe_expected(c: dict[str, Any], currency: str) -> str:
    spec = FIELDS[c["field_key"]]
    exp, op = c["expected"], c["operator"]
    unit = currency if spec.value_type == "money" else (spec.unit or "")
    if spec.value_type == "bool":
        return "是" if exp else "否"
    if spec.value_type in ("enum", "enum_multi"):
        labels = {o.value: o.label for o in spec.options}
        vals = exp if isinstance(exp, list) else [exp]
        return "、".join(labels.get(v, v) for v in vals)
    if op == "between":
        lo, hi = exp.get("min"), exp.get("max")
        if lo and hi:
            return f"{lo}–{hi} {unit}".strip()
        return f"≥ {lo} {unit}".strip() if lo else f"≤ {hi} {unit}".strip()
    sym = {"gte": "≥", "lte": "≤", "eq": "="}[op]
    return f"{sym} {exp} {unit}".strip()


def describe_actual(key: str, o: dict[str, Any]) -> str:
    v = o.get("value")
    if v is None:
        return "未知"
    spec = FIELDS.get(key)
    if isinstance(v, bool):
        return "是" if v else "否"
    if spec and spec.options:
        return {x.value: x.label for x in spec.options}.get(v, v)
    if key == "day28_gmv":
        return f"{v} {o.get('currency') or '（币种未知）'}"
    unit = spec.unit if spec and spec.unit else ""
    return f"{v}{' ' + unit if unit else ''}"


def evaluate_rule(
    c: dict[str, Any], obs: dict[str, dict[str, Any]], reporting_currency: str
) -> dict[str, Any]:
    key = c["field_key"]
    spec = FIELDS[key]
    o = obs.get(key) or {"value": None, "state": "unknown", "note": "数据源未返回"}
    res = {
        "criterion_id": c["criterion_id"],
        "field_key": key,
        "label": spec.label,
        "hardness": c["hardness"],
        "unknown_policy": c.get("unknown_policy", "keep"),
        "expected": describe_expected(c, reporting_currency),
        "actual": describe_actual(key, o),
        "status": "unknown",
        "reason": "",
    }
    if o["state"] != "known":
        res["reason"] = o.get("note") or "数据未知"
        return res
    v = o["value"]
    if spec.value_type == "money":
        cur = o.get("currency")
        if not cur:
            res["reason"] = "GMV 币种未知，不能与预算币种比较"
            return res
        if cur != reporting_currency:
            res["reason"] = f"GMV 币种为 {cur}，与任务币种 {reporting_currency} 不同，不做换算"
            return res
    if spec.value_type == "bool" or spec.value_type == "enum":
        ok = v == c["expected"]
    elif spec.value_type == "enum_multi":
        ok = v in c["expected"]
    else:
        ok = _cmp(c["operator"], Decimal(str(v)), c["expected"])
    res["status"] = "pass" if ok else "fail"
    return res


def aggregate(rules: list[dict[str, Any]]) -> tuple[str, str, int | None]:
    """返回 (hard_status, group_key, soft_score)。

    任一硬条件 fail → 排除；硬条件未知且设为“排除” → 排除；硬条件未知（保留）→ 待核实；全部 pass → 合格。
    软条件只影响排序：pass +10，fail −10；没有已知软条件时分数为 None（不当作 0 分）。
    """
    hard = [r for r in rules if r["hardness"] == "hard"]
    if any(r["status"] == "fail" for r in hard):
        hard_status = "fail"
    elif any(r["status"] == "unknown" for r in hard):
        hard_status = "unknown"
    else:
        hard_status = "pass"
    if hard_status == "fail" or any(
        r["status"] == "unknown" and r["unknown_policy"] == "exclude" for r in hard
    ):
        group = "excluded"
    elif hard_status == "unknown":
        group = "needs_verification"
    else:
        group = "qualified"
    soft_known = [r for r in rules if r["hardness"] == "soft" and r["status"] != "unknown"]
    score = sum(10 if r["status"] == "pass" else -10 for r in soft_known) if soft_known else None
    return hard_status, group, score
