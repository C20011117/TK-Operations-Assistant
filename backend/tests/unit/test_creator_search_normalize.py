"""creator_search 规范化与数据质量检查：数字类型不一致、缺失、0 值、异常单价、矛盾字段。"""

from tk_workspace.integrations.fastmoss import creator_search as cs
from tk_workspace.modules.matching.evaluate import aggregate, build_observations, evaluate_rule


def test_numbers_are_decimal_strings_and_missing_is_none():
    rec = cs.normalize_record(
        {
            "creator": {"uid": 123, "unique_id": "a", "follower_count": 12000.0},
            "commerce_summary": {"day28_gmv": "1,234.50"},
        }
    )
    assert rec["provider_uid"] == "123"
    assert rec["metrics"]["follower_count"]["value"] == "12000"
    assert rec["metrics"]["day28_gmv"]["value"] == "1234.5"
    assert rec["metrics"]["day28_units_sold"]["value"] is None  # 缺失不是 0
    assert rec["currency"] is None


def test_extract_list_handles_nested_shapes():
    assert cs.extract_list({"total": 5, "list": [{"a": 1}]}) == ([{"a": 1}], 5)
    assert cs.extract_list({"data": {"total": 3, "items": [{"a": 1}]}}) == ([{"a": 1}], 3)
    assert cs.extract_list({"total": 0, "list": []}) == ([], 0)


def test_compile_filter_uses_only_given_hard_search_fields():
    f = cs.compile_filter(
        "GB",
        [
            {"field_key": "follower_count", "operator": "gte", "expected": "10000"},
            {"field_key": "creator_type", "operator": "eq", "expected": "personal"},
        ],
    )
    assert f.model_dump(exclude_none=True) == {
        "region": "GB",
        "follower_range": {"min": 10000},
        "creator_type": 1,
    }


def _obs(gmv, units, currency="GBP", price=None):
    rec = cs.normalize_record(
        {"commerce_summary": {"day28_gmv": gmv, "day28_units_sold": units, "currency": currency}}
    )
    return build_observations(rec, "GBP", price, "GBP" if price else None)


def test_unit_price_anomaly_uses_product_price_when_known():
    assert _obs(970000, 10)["day28_gmv"]["state"] == "anomaly"
    assert _obs(3000, 150)["day28_gmv"]["state"] == "known"
    assert _obs(30000, 20, price="19.99")["day28_gmv"]["state"] == "anomaly"  # 1500/件 > 500 下限
    assert _obs(100, 0)["day28_units_sold"]["state"] == "anomaly"


def test_money_rule_unknown_when_currency_missing_or_different():
    rule = {
        "criterion_id": "x",
        "field_key": "day28_gmv",
        "operator": "gte",
        "expected": "1000",
        "hardness": "hard",
    }
    assert evaluate_rule(rule, _obs(3000, 150), "GBP")["status"] == "pass"
    assert evaluate_rule(rule, _obs(3000, 150, currency=None), "GBP")["status"] == "unknown"
    assert evaluate_rule(rule, _obs(3000, 150, currency="EUR"), "GBP")["status"] == "unknown"


def test_aggregate_three_states():
    def r(st, hard="hard", pol="keep"):
        return {"status": st, "hardness": hard, "unknown_policy": pol}

    assert aggregate([r("pass"), r("pass", "soft")])[:2] == ("pass", "qualified")
    assert aggregate([r("pass"), r("unknown")])[:2] == ("unknown", "needs_verification")
    assert aggregate([r("unknown", pol="exclude")])[:2] == ("unknown", "excluded")
    assert aggregate([r("fail"), r("pass", "soft")])[:2] == ("fail", "excluded")
    assert aggregate([r("pass")])[2] is None  # 没有软条件 → 分数未知，不是 0


# 2026-09-29 实测 creator_search（GB）返回结构，数值为虚构
REAL_SHAPE = {
    "creator": {
        "uid": "7000000000000000001",
        "unique_id": "demo_handle",
        "nickname": "",
        "region": "GB",
        "follower_count": 146095,
        "creator_type_code": 2,
        "account_type_code": 0,
        "video_count_total": 812,
        "category": {"id": 9, "name": "Home, Furniture & Appliances"},
    },
    "commerce_summary": {
        "currency": "GBP",
        "day28_gmv": 140069.18,
        "day28_units_sold": 5124,
        "engagement_rate_percent": 0.76,
        "has_email": True,
        "total_units_sold": 23623,
        "video_units_sold": 31613,
        "trend_categories": [{"id": 1, "name": "Fashion"}, {"id": 2, "name": "Electronics"}],
    },
    "audience_summary": {
        "age_distribution": [
            {"age_range": "18-24", "percentage": 20.0},
            {"age_range": "25-34", "percentage": 30.0},
            {"age_range": "35-44", "percentage": 20.0},
            {"age_range": "45-54", "percentage": 15.0},
            {"age_range": "55+", "percentage": 15.0},
        ],
        "gender_distribution": [{"gender": "female", "percentage": 60.0}],
    },
}


def test_real_shape_fields_are_mapped():
    rec = cs.normalize_record(REAL_SHAPE)
    assert rec["currency"] == "GBP" and rec["region"] == "GB"
    assert rec["flags"]["creator_type"] == "shop"
    assert rec["metrics"]["video_count"]["value"] == "812"
    assert rec["metrics"]["engagement_rate"]["value"] == "0.76"
    # 35-44 + 45-54 + 55+ = 50% > 25-34 的 30%
    assert rec["audience"]["top_age"] == "35+"
    # 昵称为空时不能误取类目对象的 name
    assert rec["nickname"] is None
    assert rec["categories"] == "账号类目：Home, Furniture & Appliances；近期带货类目：Fashion、Electronics"
    # 视频销量大于累计销量在实测中很常见，不算矛盾
    obs = build_observations(rec, "GBP", None, None)
    assert obs["total_units_sold"]["state"] == "known"
    assert obs["categories"]["value"].startswith("账号类目")


def test_age_bucket_edge_cases():
    assert cs._top_age_bucket([]) is None
    assert cs._top_age_bucket([{"age_range": "13-17", "percentage": 90}]) is None
    assert cs._top_age_bucket([{"age_range": "18-24", "percentage": 0}]) is None
    assert cs._top_age_bucket([{"age_range": "18-24", "percentage": "51.2"}]) == "18-24"


def test_ecommerce_flag_guaranteed_by_search_filter():
    rec = cs.normalize_record(REAL_SHAPE)
    assert rec["flags"]["is_ecommerce_creator"] is None
    obs = build_observations({**rec, "filter_guaranteed": {"is_ecommerce_creator": True}}, "GBP", None, None)
    assert obs["is_ecommerce_creator"]["value"] is True
    assert obs["is_ecommerce_creator"]["state"] == "known"
    assert "搜索条件" in obs["is_ecommerce_creator"]["note"]


def test_soft_tiebreak_follows_criteria_order():
    from tk_workspace.modules.matching.pipeline import soft_tiebreak

    def rules(*st):
        return [{"hardness": "hard", "status": "pass"}] + [{"hardness": "soft", "status": s} for s in st]

    gmv_pass = rules("pass", "fail")  # 得分 0：通过第一条软条件（GMV）
    er_pass = rules("fail", "pass")  # 得分 0：通过第二条软条件（互动率）
    unknown_first = rules("unknown", "pass")
    ordered = sorted([er_pass, unknown_first, gmv_pass], key=soft_tiebreak)
    assert ordered == [gmv_pass, unknown_first, er_pass]
