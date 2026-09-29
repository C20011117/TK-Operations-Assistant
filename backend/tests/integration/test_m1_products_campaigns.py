"""M1 验收：修改产品产生新版本；任务固定引用版本；英国/德国币种与时区正确；缺少必填条件时不能确认；不同站点任务互不影响。"""

import pytest

UK_TERMS = {
    "market_code": "UK",
    "price_status": "known",
    "price_amount": "19.99",
    "sample_policy": "free",
    "commission_min_pct": "10",
    "commission_max_pct": "20",
}
DE_TERMS = {"market_code": "DE", "price_status": "unknown", "sample_policy": "unknown"}
FACTS = {
    "summary": "便携榨汁杯，USB-C 充电，30 秒出汁",
    "selling_points": ["便携", "30 秒出汁"],
    "use_scenarios": ["办公室", "健身后"],
    "forbidden_claims": ["不得宣称减肥功效"],
}
CRITERIA = [
    {
        "field_key": "follower_count",
        "operator": "between",
        "expected": {"min": 10000, "max": 500000},
        "hardness": "hard",
        "unknown_policy": "exclude",
    },
    {"field_key": "is_ecommerce_creator", "operator": "eq", "expected": True, "hardness": "hard"},
    {"field_key": "day28_gmv", "operator": "gte", "expected": "1000", "hardness": "soft"},
]


def _product(client, terms=(UK_TERMS, DE_TERMS), sku="JC-01") -> dict:
    p = client.post("/api/v1/products", json={"sku": sku, "name": "便携榨汁杯"}).json()
    r = client.put(f"/api/v1/products/{p['id']}/draft", json={"facts": FACTS, "market_terms": list(terms)})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/products/{p['id']}/draft/confirm")
    assert r.status_code == 200, r.text
    return r.json()


def _campaign(client, product_id, market="UK", **market_settings) -> dict:
    r = client.post(
        "/api/v1/campaigns",
        json={
            "product_id": product_id,
            "name": f"榨汁杯 {market} 首批",
            "goal": "sales",
            "collaboration_type": "free_sample",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "market_code": market,
            "market": market_settings,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ready(client, c, criteria=CRITERIA, keywords=("juicer",)) -> dict:
    cm = c["markets"][0]
    client.put(
        f"/api/v1/campaign-markets/{cm['id']}",
        json={"target_list_size": 30, "budget_min": "200", "budget_max": "800", "cost_cap_credits": 60},
    )
    r = client.put(
        f"/api/v1/campaign-markets/{cm['id']}/criteria",
        json={"criteria": list(criteria), "search": {"keywords": list(keywords)}},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/campaign-markets/{cm['id']}/confirm")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- 产品版本 ----------------


def test_new_product_starts_with_blank_draft_and_incomplete_draft_cannot_be_confirmed(client):
    p = client.post("/api/v1/products", json={"sku": "A1", "name": "测试"}).json()
    assert p["current"] is None and p["draft"]["version_no"] == 1
    r = client.post(f"/api/v1/products/{p['id']}/draft/confirm")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "draft_incomplete"
    assert len(r.json()["error"]["problems"]) == 3


def test_price_currency_comes_from_market_and_unknown_price_is_not_zero(client):
    p = _product(client)
    terms = {t["market_code"]: t for t in p["current"]["market_terms"]}
    assert terms["UK"]["price_currency"] == "GBP" and terms["UK"]["price_amount"] == "19.99"
    assert terms["DE"]["price_currency"] == "EUR"
    assert terms["DE"]["price_status"] == "unknown" and terms["DE"]["price_amount"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"market_code": "UK", "price_status": "known"},  # 已知但没填金额
        {"market_code": "UK", "price_status": "unknown", "price_amount": "0"},  # 未知却填了 0
        {"market_code": "UK", "price_status": "known", "price_amount": "12.345"},  # 超过两位小数
        {"market_code": "UK", "price_status": "known", "price_amount": "-1"},
        {
            "market_code": "UK",
            "price_status": "known",
            "price_amount": "10",
            "commission_min_pct": "30",
            "commission_max_pct": "20",
        },
    ],
)
def test_invalid_market_terms_rejected(client, bad):
    p = client.post("/api/v1/products", json={"sku": "B1", "name": "测试"}).json()
    r = client.put(f"/api/v1/products/{p['id']}/draft", json={"facts": FACTS, "market_terms": [bad]})
    assert r.status_code == 422


def test_editing_product_creates_new_version_and_old_version_is_unchanged(client):
    p = _product(client)
    v1 = p["current"]
    assert v1["version_no"] == 1 and v1["status"] == "confirmed"

    # 没有改动的草稿不能确认
    client.post(f"/api/v1/products/{p['id']}/draft")
    r = client.post(f"/api/v1/products/{p['id']}/draft/confirm")
    assert r.json()["error"]["code"] == "no_changes"

    new_uk = {**UK_TERMS, "price_amount": "24.99"}
    client.put(
        f"/api/v1/products/{p['id']}/draft",
        json={"facts": {**FACTS, "selling_points": ["便携", "静音"]}, "market_terms": [new_uk, DE_TERMS]},
    )
    p2 = client.post(f"/api/v1/products/{p['id']}/draft/confirm").json()
    assert p2["current"]["version_no"] == 2
    assert [(v["version_no"], v["status"]) for v in p2["versions"]] == [(2, "confirmed"), (1, "superseded")]

    old = client.get(f"/api/v1/products/versions/{v1['id']}").json()
    assert old["facts"]["selling_points"] == ["便携", "30 秒出汁"]
    assert {t["market_code"]: t["price_amount"] for t in old["market_terms"]}["UK"] == "19.99"


def test_sku_must_be_unique_among_active_products(client):
    client.post("/api/v1/products", json={"sku": "DUP", "name": "一"})
    r = client.post("/api/v1/products", json={"sku": "DUP", "name": "二"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sku_exists"


# ---------------- 任务 ----------------


def test_campaign_requires_confirmed_product(client):
    p = client.post("/api/v1/products", json={"sku": "C1", "name": "未确认"}).json()
    r = client.post(
        "/api/v1/campaigns",
        json={
            "product_id": p["id"],
            "name": "x",
            "goal": "sales",
            "collaboration_type": "paid",
            "market_code": "UK",
        },
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "product_unconfirmed"


def test_uk_and_de_campaigns_have_correct_currency_and_time_zone(client):
    p = _product(client)
    uk = _campaign(client, p["id"], "UK")["markets"][0]
    de = _campaign(client, p["id"], "DE")["markets"][0]
    assert (uk["reporting_currency"], uk["time_zone"], uk["fastmoss_region"]) == (
        "GBP",
        "Europe/London",
        "GB",
    )
    assert (de["reporting_currency"], de["time_zone"]) == ("EUR", "Europe/Berlin")
    assert uk["price_term"]["price_amount"] == "19.99"
    assert any(w["code"] == "price_unknown" for w in de["readiness"]["warnings"])


def test_missing_required_items_block_confirmation(client):
    p = _product(client)
    c = _campaign(client, p["id"])
    cm = c["markets"][0]
    codes = {b["code"] for b in cm["readiness"]["blockers"]}
    assert codes == {"target_list_size_missing", "cost_cap_missing", "criteria_empty"}
    assert cm["readiness"]["ready"] is False

    r = client.post(f"/api/v1/campaign-markets/{cm['id']}/confirm")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "not_ready" and {b["code"] for b in err["blockers"]} == codes
    assert client.get(f"/api/v1/campaigns/{c['id']}").json()["markets"][0]["status"] == "draft"


def test_market_without_price_term_is_blocked(client):
    p = _product(client, terms=(UK_TERMS,))
    cm = _campaign(client, p["id"], "FR")["markets"][0]
    assert "price_term_missing" in {b["code"] for b in cm["readiness"]["blockers"]}


def test_manual_import_market_does_not_need_cost_cap(client):
    p = _product(
        client, terms=(UK_TERMS, {"market_code": "IE", "price_status": "known", "price_amount": "22"})
    )
    cm = _campaign(client, p["id"], "IE", target_list_size=10)["markets"][0]
    codes = {b["code"] for b in cm["readiness"]["blockers"]}
    assert "cost_cap_missing" not in codes
    assert "manual_import_only" in {w["code"] for w in cm["readiness"]["warnings"]}


def test_confirm_freezes_criteria_and_product_version(client):
    p = _product(client)
    c = _ready(client, _campaign(client, p["id"]))
    cm = c["markets"][0]
    assert cm["status"] == "ready"
    cur = cm["criteria_current"]
    assert cur["version_no"] == 1 and cur["status"] == "confirmed" and cur["product_version_no"] == 1
    assert cm["criteria_draft"] is None
    by_key = {x["field_key"]: x for x in cur["criteria"]}
    assert by_key["follower_count"]["expected"] == {"min": "10000", "max": "500000"}  # 数字统一为字符串
    assert by_key["day28_gmv"]["expected"] == "1000"


def test_campaign_keeps_its_product_version_until_user_updates(client):
    p = _product(client)
    c = _ready(client, _campaign(client, p["id"]))
    cm_id = c["markets"][0]["id"]

    client.post(f"/api/v1/products/{p['id']}/draft")
    client.put(
        f"/api/v1/products/{p['id']}/draft",
        json={"facts": FACTS, "market_terms": [{**UK_TERMS, "price_amount": "29.99"}, DE_TERMS]},
    )
    client.post(f"/api/v1/products/{p['id']}/draft/confirm")

    cm = client.get(f"/api/v1/campaigns/{c['id']}").json()["markets"][0]
    assert cm["product_version_no"] == 1 and cm["latest_product_version_no"] == 2
    assert cm["price_term"]["price_amount"] == "19.99"  # 仍是任务创建时的价格
    assert cm["status"] == "ready"
    assert "product_version_outdated" in {w["code"] for w in cm["readiness"]["warnings"]}

    cm = client.post(f"/api/v1/campaign-markets/{cm_id}/use-latest-product").json()["markets"][0]
    assert cm["status"] == "draft" and cm["product_version_no"] == 2
    assert cm["price_term"]["price_amount"] == "29.99"
    assert cm["criteria_draft"]["product_version_no"] == 2
    assert cm["criteria_current"]["product_version_no"] == 1  # 旧确认版本仍引用 v1

    cm = client.post(f"/api/v1/campaign-markets/{cm_id}/confirm").json()["markets"][0]
    assert [(h["version_no"], h["status"], h["product_version_no"]) for h in cm["criteria_history"]] == [
        (2, "confirmed", 2),
        (1, "superseded", 1),
    ]


def test_changing_settings_after_confirmation_requires_reconfirm(client):
    p = _product(client)
    c = _ready(client, _campaign(client, p["id"]))
    cm_id = c["markets"][0]["id"]
    r = client.put(f"/api/v1/campaign-markets/{cm_id}", json={"target_list_size": 50, "cost_cap_credits": 60})
    assert r.json()["markets"][0]["status"] == "draft"
    cm = client.post(f"/api/v1/campaign-markets/{cm_id}/confirm").json()["markets"][0]
    assert cm["status"] == "ready" and cm["criteria_current"]["version_no"] == 1  # 条件没变，不产生新版本


@pytest.mark.parametrize(
    ("criterion", "fragment"),
    [
        (
            {"field_key": "creator_city", "operator": "eq", "expected": "x", "hardness": "hard"},
            "不支持的条件字段",
        ),
        (
            {"field_key": "follower_count", "operator": "in", "expected": [1], "hardness": "hard"},
            "不支持该比较方式",
        ),
        (
            {
                "field_key": "follower_count",
                "operator": "between",
                "expected": {"min": 9, "max": 1},
                "hardness": "hard",
            },
            "取值不正确",
        ),
        (
            {
                "field_key": "day28_gmv",
                "operator": "gte",
                "expected": "10",
                "hardness": "soft",
                "unknown_policy": "exclude",
            },
            "未知时不能直接排除",
        ),
        (
            {"field_key": "content_language", "operator": "in", "expected": ["fr"], "hardness": "soft"},
            "不是该站点的常用语言",
        ),
        (
            {"field_key": "engagement_rate", "operator": "gte", "expected": 120, "hardness": "soft"},
            "取值不正确",
        ),
        (
            {"field_key": "is_ecommerce_creator", "operator": "eq", "expected": "yes", "hardness": "hard"},
            "取值不正确",
        ),
    ],
)
def test_criteria_whitelist_and_value_validation(client, criterion, fragment):
    p = _product(client)
    cm_id = _campaign(client, p["id"])["markets"][0]["id"]
    r = client.put(f"/api/v1/campaign-markets/{cm_id}/criteria", json={"criteria": [criterion]})
    assert r.status_code == 422
    assert fragment in r.json()["error"]["message"]


def test_campaigns_on_different_markets_do_not_affect_each_other(client):
    p = _product(client)
    uk = _ready(client, _campaign(client, p["id"], "UK"))
    de = _campaign(client, p["id"], "DE")
    de_cm = de["markets"][0]["id"]
    client.put(
        f"/api/v1/campaign-markets/{de_cm}/criteria",
        json={
            "criteria": [
                {"field_key": "content_language", "operator": "in", "expected": ["de"], "hardness": "hard"}
            ]
        },
    )
    uk_after = client.get(f"/api/v1/campaigns/{uk['id']}").json()["markets"][0]
    assert uk_after["status"] == "ready"
    assert {c["field_key"] for c in uk_after["criteria_current"]["criteria"]} == {
        "follower_count",
        "is_ecommerce_creator",
        "day28_gmv",
    }
    listing = client.get("/api/v1/campaigns", params={"product_id": p["id"]}).json()
    assert sorted((x["market_code"], x["reporting_currency"]) for x in listing) == [
        ("DE", "EUR"),
        ("UK", "GBP"),
    ]


def test_duplicate_to_other_market_drops_site_specific_parts(client):
    p = _product(client)
    uk = _ready(
        client,
        _campaign(client, p["id"], "UK"),
        criteria=[
            *CRITERIA,
            {"field_key": "content_language", "operator": "in", "expected": ["en"], "hardness": "soft"},
        ],
    )
    r = client.post(f"/api/v1/campaigns/{uk['id']}/duplicate", json={"market_code": "DE"})
    assert r.status_code == 201
    de = r.json()["markets"][0]
    assert (de["reporting_currency"], de["time_zone"]) == ("EUR", "Europe/Berlin")
    assert de["budget_min"] is None and de["budget_max"] is None  # 币种不同，预算不复制
    assert de["target_list_size"] == 30 and de["cost_cap_credits"] == 60
    keys = {c["field_key"] for c in de["criteria_draft"]["criteria"]}
    assert keys == {"follower_count", "is_ecommerce_creator"}
    assert de["criteria_draft"]["search"]["keywords"] == ["juicer"]
    assert de["status"] == "draft"


def test_criteria_fields_catalog_marks_provider_support(client):
    fields = {f["key"]: f for f in client.get("/api/v1/criteria/fields").json()}
    assert fields["follower_count"]["support"] == "search_filter"
    assert fields["day28_gmv"]["support"] == "result_field"
    assert fields["content_language"]["support"] == "not_provided"


def test_archived_campaign_cannot_be_modified(client):
    p = _product(client)
    c = _campaign(client, p["id"])
    client.post(f"/api/v1/campaigns/{c['id']}/archive")
    r = client.put(f"/api/v1/campaign-markets/{c['markets'][0]['id']}", json={"target_list_size": 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "archived"
    assert client.get("/api/v1/campaigns").json() == []
