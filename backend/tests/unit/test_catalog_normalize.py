"""M2.1：类目 / 竞品 / 竞品达人结果规范化，商品链接解析，多来源记录合并。"""

from tk_workspace.integrations.fastmoss import catalog
from tk_workspace.modules.matching.pipeline import _category_filter, merge_records


def test_parse_product_ref():
    pid = "1729758894506678836"
    assert catalog.parse_product_ref(pid) == pid
    assert catalog.parse_product_ref(f"https://shop.tiktok.com/view/product/{pid}?region=GB") == pid
    assert catalog.parse_product_ref(f"https://www.tiktok.com/x?product_id={pid}&a=1") == pid
    assert catalog.parse_product_ref("12345") is None
    assert catalog.parse_product_ref("abc") is None


def test_linked_creators_are_partial_and_missing_is_unknown():
    data = {
        "linked_creators": {
            "list": [
                {
                    "creator": {
                        "creator_uid": "7",
                        "creator_handle": "@cam",
                        "region": "gb",
                        "follower_count": 1200,
                    },
                    "product_contribution": {"product_gmv": 88.5, "product_units_sold": 3},
                    "audience_summary": {"age_distribution": []},
                },
                {"creator": {}},  # 没有 uid 和 handle → 丢弃
            ],
            "total": 2,
        }
    }
    params = catalog.ProductCreatorsParams(
        filter=catalog.ProductCreatorsFilter(product_id="1729758894506678836")
    )
    recs, total = catalog.normalize_linked_creators(data, params)
    assert total == 2 and len(recs) == 1
    r = recs[0]
    assert r["unique_id"] == "cam" and r["region"] == "GB" and r["partial"] is True
    assert r["metrics"]["follower_count"]["value"] == "1200"
    assert r["metrics"]["day28_gmv"]["value"] is None  # 竞品达人没有近 28 天数据：未知，不是 0
    assert r["competitor_sales"] == [
        {"product_id": "1729758894506678836", "product_gmv": "88.5", "product_units_sold": "3"}
    ]


def test_products_skip_invalid_ids():
    recs, total = catalog.normalize_products(
        {
            "list": [{"product": {"product_id": "x"}}, {"product": {"product_id": "1729758894506678836"}}],
            "total": 2,
        }
    )
    assert [r["product_id"] for r in recs] == ["1729758894506678836"] and total == 2
    assert recs[0]["day28_units_sold"] is None


def test_merge_prefers_full_record_and_unions_sources():
    partial = {
        "provider_uid": "u1",
        "partial": True,
        "categories": "账号类目：IT",
        "competitor_sales": [{"product_id": "A", "product_gmv": "10"}],
        "metrics": {"day28_gmv": {"value": None}},
    }
    full = {
        "provider_uid": "u1",
        "currency": "GBP",
        "categories": None,
        "category_guaranteed": "手机与数码",
        "competitor_sales": [{"product_id": "B"}],
        "metrics": {"day28_gmv": {"value": "300"}},
    }
    for merged in (merge_records(partial, full), merge_records(full, partial)):
        assert merged["partial"] is False
        assert merged["metrics"]["day28_gmv"]["value"] == "300"
        assert {x["product_id"] for x in merged["competitor_sales"]} == {"A", "B"}
        assert merged["category_guaranteed"] == "手机与数码"
        assert merged["categories"] == "账号类目：IT"  # 完整记录缺的字段用另一条补上


def test_category_filter_levels():
    cat = {"l1_id": 16, "l2_id": 909192, "l3_id": 911752, "path": "手机与数码-摄影摄像-监控摄像设备"}
    assert _category_filter(cat, "l3") == (
        {"product_category_l3_id": 911752},
        "手机与数码 › 摄影摄像 › 监控摄像设备",
    )
    assert _category_filter(cat, "l1") == ({"product_category_l1_id": 16}, "手机与数码")
    assert _category_filter({**cat, "l3_id": None}, "l3")[0] == {"product_category_l2_id": 909192}
    assert _category_filter(None, "l3") == ({}, None)
