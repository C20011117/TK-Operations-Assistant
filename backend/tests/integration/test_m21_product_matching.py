"""M2.1 按产品找达人：类目识别、按类目搜索、竞品找达人与数据补全、类目匹配显示、额度上限。

FastMoss 用按工具分派的假客户端，大模型用假函数，不消耗任何额度。
"""

import json
import uuid

import pytest
from sqlalchemy import text

from tk_workspace.integrations.fastmoss.mcp_client import Charge, ToolResult
from tk_workspace.modules.matching import assess, provider
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.jobs.runner import JobRunner, wait_until

from .test_m2_matching import _fake_model, creator

CAMERA = {
    "l1_id": 16,
    "l2_id": 909192,
    "l3_id": 911752,
    "path": "手机与数码-摄影摄像-监控摄像设备",
}
COMP_ID = "1729758894506678836"
COSTS = {
    "creator_search": 1,
    "product_search": 1,
    "product_creator_analysis": 3,
    "search_category_by_words": 0,
}


def linked(n, region="GB", gmv=5000.0, units=200):
    return {
        "creator": {
            "creator_uid": f"uid{n}",
            "creator_handle": f"creator{n}",
            "creator_name": f"Creator {n}",
            "creator_category": {"id": 3, "name": "IT & High-Tech"},
            "follower_count": 80000,
            "region": region,
        },
        "product_contribution": {"product_gmv": gmv, "product_units_sold": units},
        "creator_cumulative_performance": {},
        "audience_summary": {"age_distribution": [], "gender_distribution": []},
    }


class FakeCatalog:
    """按工具名返回数据；记录每次调用的工具和参数。"""

    def __init__(self, category_pages=None, keyword_pages=None, linked_creators=None):
        self.category_pages = category_pages or []
        self.keyword_pages = keyword_pages or []
        self.linked_creators = linked_creators or {}
        self.calls = []

    def __call__(self):
        return self

    def _result(self, name, data, n):
        cost = COSTS[name] if n else 0
        return ToolResult(
            tool=name,
            data=data,
            charge=Charge(charged=bool(cost), credit_cost=cost, remaining_credits=70),
            request_id=f"req-{len(self.calls)}",
        )

    def call_tool(self, name, arguments=None):
        args = json.loads(json.dumps(arguments or {}))
        self.calls.append((name, args))
        if name == "search_category_by_words":
            cats = [
                {
                    "category_id_level1": 16,
                    "category_id_level2": 909192,
                    "category_id_level3": 911752,
                    "cn_name": "监控摄像设备",
                    "cn_full_name": CAMERA["path"],
                    "score": 0.63,
                    "matched_query": "摄像头",
                },
                {
                    "category_id_level1": 15,
                    "category_id_level2": 826760,
                    "category_id_level3": 827144,
                    "cn_name": "视讯摄像机",
                    "cn_full_name": "电脑办公-外设产品与配件-视讯摄像机",
                    "score": 0.65,
                    "matched_query": "摄像头",
                },
            ]
            return self._result(name, {"params": args, "result": {"categories": cats, "total": 2}}, 0)
        if name == "product_search":
            items = [
                {
                    "product": {
                        "product_id": COMP_ID,
                        "title": "HomiQ 2K Window Camera",
                        "category": {
                            "l1": {"name": "Phones & Electronics"},
                            "l3": {"name": "Security Cameras"},
                        },
                        "currency_code": "GBP",
                        "price_display": "£33.99",
                    },
                    "sales_summary": {"last_28d_units_sold": 3767, "last_28d_gmv": 125673},
                    "distribution_summary": {"linked_creator_count": 814},
                    "shop": {"shop_name": "HomiQ"},
                }
            ]
            return self._result(name, {"list": items, "total": 1}, 1)
        if name == "product_creator_analysis":
            lst = self.linked_creators.get(args["filter"]["product_id"], [])
            return self._result(
                name, {"creator_summary": {}, "linked_creators": {"list": lst, "total": len(lst)}}, 1
            )
        # creator_search
        flt = args.get("filter", {})
        if flt.get("uid"):
            n = int(flt["uid"].removeprefix("uid"))
            recs = [creator(n, gmv=4000)]
        else:
            pages = (
                self.category_pages
                if any(k.startswith("product_category_") for k in flt)
                else self.keyword_pages
            )
            page = args.get("page", 1)
            recs = pages[page - 1] if page <= len(pages) else []
        return self._result(name, {"total": 1000 if recs else 0, "list": recs}, len(recs))

    def close(self):
        pass


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


@pytest.fixture
def fake(monkeypatch):
    def install(fm):
        provider.set_client_factory(fm)
        return fm

    monkeypatch.setattr(provider.limiter, "interval", 0)
    monkeypatch.setattr(assess, "call_model", _fake_model)
    yield install
    provider.set_client_factory(None)


def _product(client, category=CAMERA):
    p = client.post(
        "/api/v1/products", json={"sku": f"CAM-{uuid.uuid4().hex[:6]}", "name": "家用摄像头"}
    ).json()
    facts = {"summary": "2K 家用安防摄像头", "selling_points": ["夜视"], "use_scenarios": ["家庭安防"]}
    if category:
        facts["category"] = category
    terms = [{"market_code": "UK", "price_status": "known", "price_amount": "29.99", "sample_policy": "free"}]
    r = client.put(f"/api/v1/products/{p['id']}/draft", json={"facts": facts, "market_terms": terms})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/v1/products/{p['id']}/draft/confirm").status_code == 200
    return p["id"]


def _campaign(client, product_id, search, criteria=None, cap=10, target=5, confirm=True):
    c = client.post(
        "/api/v1/campaigns",
        json={
            "product_id": product_id,
            "name": "摄像头",
            "goal": "sales",
            "collaboration_type": "free_sample",
            "market_code": "UK",
            "market": {},
        },
    ).json()
    cm_id = c["markets"][0]["id"]
    client.put(
        f"/api/v1/campaign-markets/{cm_id}", json={"target_list_size": target, "cost_cap_credits": cap}
    )
    crit = (
        criteria
        if criteria is not None
        else [
            {
                "field_key": "follower_count",
                "operator": "between",
                "expected": {"min": 10000, "max": 500000},
                "hardness": "hard",
            }
        ]
    )
    r = client.put(f"/api/v1/campaign-markets/{cm_id}/criteria", json={"criteria": crit, "search": search})
    assert r.status_code == 200, r.text
    if confirm:
        r = client.post(f"/api/v1/campaign-markets/{cm_id}/confirm")
        assert r.status_code == 200, r.text
    return cm_id


def _view(client, cm_id):
    for c in client.get("/api/v1/campaigns").json():
        detail = client.get(f"/api/v1/campaigns/{c['id']}").json()
        for m in detail["markets"]:
            if m["id"] == cm_id:
                return m
    raise AssertionError(cm_id)


def _run(client, cm_id):
    r = client.post(
        f"/api/v1/campaign-markets/{cm_id}/matching-runs", headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    assert wait_until(
        lambda: client.get(f"/api/v1/matching-runs/{rid}").json()["status"] not in ("queued", "running"),
        timeout=20,
    )
    return client.get(f"/api/v1/matching-runs/{rid}/recommendations").json()


def _card(rec, handle):
    return next(c for c in rec["cards"] if c["creator"]["unique_id"] == handle)


# ---------------- 类目识别与产品类目 ----------------


def test_category_suggestions_are_free_and_normalized(client, fake):
    fm = fake(FakeCatalog())
    r = client.post("/api/v1/products/category-suggestions", json={"query": ["摄像头", "security camera"]})
    assert r.status_code == 200, r.text
    items = r.json()
    assert items[0]["l3_id"] == 827144 and items[0]["path"].startswith("电脑办公")  # 按相关度排序
    assert {"l1_id": 16, "l2_id": 909192, "l3_id": 911752} == {
        k: items[1][k] for k in ("l1_id", "l2_id", "l3_id")
    }
    assert fm.calls[0][0] == "search_category_by_words"
    with tx() as s:
        assert s.execute(text("SELECT COUNT(*) FROM usage_ledger")).scalar() == 0


def test_readiness_warns_without_category_and_category_alone_is_enough(client, fake):
    fake(FakeCatalog())
    no_cat = _campaign(client, _product(client, category=None), {"keywords": []}, criteria=[], confirm=False)
    view = _view(client, no_cat)
    codes = {w["code"] for w in view["readiness"]["warnings"]} | {
        b["code"] for b in view["readiness"]["blockers"]
    }
    assert {"product_category_missing", "search_scope_broad", "criteria_empty"} <= codes

    with_cat = _campaign(client, _product(client), {"keywords": []}, criteria=[], confirm=False)
    view = _view(client, with_cat)
    codes = {w["code"] for w in view["readiness"]["warnings"]} | {
        b["code"] for b in view["readiness"]["blockers"]
    }
    assert not codes & {"product_category_missing", "search_scope_broad", "criteria_empty"}


# ---------------- 按类目搜索 ----------------


def test_search_uses_product_category_and_card_shows_match(client, runner, fake):
    fm = fake(FakeCatalog(category_pages=[[creator(1), creator(2)]]))
    cm_id = _campaign(client, _product(client), {"keywords": []})
    rec = _run(client, cm_id)
    searches = [a for n, a in fm.calls if n == "creator_search"]
    assert searches[0]["filter"]["product_category_l3_id"] == 911752
    assert searches[0]["orderby"] == [{"field": "day28_gmv", "order": "desc"}]  # 没有关键词时按 GMV 取
    card = _card(rec, "creator1")
    line = next(m for m in card["matches"] if m.get("tag") == "类目")
    assert "带过本产品类目：是" in line["text"] and "监控摄像设备" in line["text"]
    assert any("不代表是其主营方向" in x for x in rec["snapshot"]["limitations"])


def test_category_search_runs_alone_before_keyword_search(client, runner, fake):
    # 实测类目 + 关键词叠加几乎总是 0 结果：类目单独先搜，关键词搜索在后且不带类目
    fm = fake(
        FakeCatalog(category_pages=[[creator(1), creator(2)]], keyword_pages=[[creator(2), creator(3)]])
    )
    rec = _run(client, _campaign(client, _product(client), {"keywords": ["security camera"]}))
    searches = [a for n, a in fm.calls if n == "creator_search"]
    assert len(searches) == 2
    assert searches[0]["filter"]["product_category_l3_id"] == 911752 and not searches[0].get("keywords")
    assert searches[1].get("keywords") == "security camera"
    assert not any(k.startswith("product_category_") for k in searches[1]["filter"])
    assert any(m.get("tag") == "类目" for m in _card(rec, "creator2")["matches"])  # 两路都找到：保留类目证据
    c3 = _card(rec, "creator3")
    assert not any(m.get("tag") == "类目" for m in c3["matches"])
    assert any(u.get("tag") == "类目" and "未知" in u["text"] for u in c3["unknowns"])


def test_category_level_setting_and_fallback(client, runner, fake):
    fm = fake(FakeCatalog(category_pages=[[creator(1)]]))
    _run(client, _campaign(client, _product(client), {"keywords": [], "category_level": "l2"}))
    assert [a for n, a in fm.calls if n == "creator_search"][0]["filter"]["product_category_l2_id"] == 909192

    fm2 = fake(FakeCatalog(category_pages=[[creator(1)]]))
    two_level = {"l1_id": 16, "l2_id": 909192, "path": "手机与数码-摄影摄像"}
    _run(client, _campaign(client, _product(client, category=two_level), {"keywords": []}))
    flt = [a for n, a in fm2.calls if n == "creator_search"][0]["filter"]
    assert flt["product_category_l2_id"] == 909192 and "product_category_l3_id" not in flt


def test_keyword_only_match_is_unknown(client, runner, fake):
    fake(FakeCatalog(keyword_pages=[[creator(1)]]))
    cm_id = _campaign(client, _product(client), {"keywords": ["camera"], "use_product_category": False})
    card = _card(_run(client, cm_id), "creator1")
    assert any(u.get("tag") == "类目" and "未知" in u["text"] for u in card["unknowns"])


# ---------------- 竞品找达人 ----------------


def test_competitor_creators_enriched_ranked_first_and_other_regions_skipped(client, runner, fake):
    fm = fake(
        FakeCatalog(
            category_pages=[[creator(10), creator(11)]],
            linked_creators={
                COMP_ID: [linked(1, gmv=9000), linked(2, gmv=7000), linked(3, gmv=100), linked(4, "DE")]
            },
        )
    )
    search = {
        "keywords": [],
        "competitors": [{"product_id": COMP_ID, "title": "HomiQ 2K Window Camera", "currency": "GBP"}],
    }
    cm_id = _campaign(client, _product(client), search, cap=20, target=2)
    rec = _run(client, cm_id)
    names = [n for n, _ in fm.calls]
    assert names[0] == "product_creator_analysis"
    uid_calls = [a["filter"]["uid"] for n, a in fm.calls if n == "creator_search" and "uid" in a["filter"]]
    assert uid_calls == ["uid1", "uid2"]  # 按竞品 GMV 从高到低，只补全目标名单数（2）
    handles = [c["creator"]["unique_id"] for c in rec["cards"]]
    assert "creator4" not in handles  # 德国达人不进入英国任务
    assert rec["run"]["counters"]["other_region_skipped"] == 1
    assert rec["run"]["credits_used"] == 3 + 2 + 1

    c1 = _card(rec, "creator1")
    assert any(m.get("tag") == "竞品" and "HomiQ" in m["text"] and "GBP" in m["text"] for m in c1["matches"])
    assert c1["metrics"]["day28_gmv"]["value"] == "4000"  # 已补全
    c3 = _card(rec, "creator3")
    assert (
        c3["metrics"]["day28_gmv"]["state"] == "unknown"
        and c3["metrics"]["follower_count"]["value"] == "80000"
    )
    assert any("没有补全近 28 天数据" in x for x in rec["snapshot"]["limitations"])
    # 同样合格、同样 AI 结论时，卖过竞品的排在只带过类目的前面
    qualified = [c["creator"]["unique_id"] for c in rec["cards"] if c["group"] == "qualified"]
    assert qualified.index("creator1") < qualified.index("creator10")


def test_competitor_cost_counts_against_cap(client, runner, fake):
    fm = fake(FakeCatalog(category_pages=[[creator(10)]], linked_creators={COMP_ID: [linked(1), linked(2)]}))
    cm_id = _campaign(client, _product(client), {"competitors": [{"product_id": COMP_ID}]}, cap=3)
    rec = _run(client, cm_id)
    assert [n for n, _ in fm.calls] == ["product_creator_analysis"]
    assert rec["run"]["credits_used"] == 3
    assert rec["run"]["stop_reason"] == "cost_cap_reached" and rec["run"]["status"] == "partial"


def test_competitor_link_is_parsed_and_invalid_rejected(client, fake):
    fake(FakeCatalog())
    pid = _product(client)
    url = f"https://shop.tiktok.com/view/product/{COMP_ID}?region=GB&locale=en"
    cm_id = _campaign(
        client, pid, {"competitors": [{"product_id": url}, {"product_id": COMP_ID}]}, confirm=False
    )
    view = _view(client, cm_id)
    assert view["criteria_draft"]["search"]["competitors"] == [
        {"product_id": COMP_ID, "title": None, "currency": None}
    ]  # 链接解析成编号，重复的去掉
    r = client.put(
        f"/api/v1/campaign-markets/{cm_id}/criteria",
        json={"criteria": [], "search": {"competitors": [{"product_id": "not-a-product"}]}},
    )
    assert r.status_code == 422


def test_competitor_suggestions_cost_one_credit_and_need_category(client, fake):
    fm = fake(FakeCatalog())
    cm_id = _campaign(client, _product(client), {"keywords": []}, confirm=False)
    r = client.post(f"/api/v1/campaign-markets/{cm_id}/competitor-suggestions", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credits_used"] == 1 and body["items"][0]["product_id"] == COMP_ID
    assert body["items"][0]["currency"] == "GBP" and body["category_path"] == CAMERA["path"]
    assert fm.calls[-1][1]["filter"] == {"region": "GB", "category_path": [16, 909192, 911752]}
    with tx() as s:
        row = s.execute(text("SELECT run_id, operation, credits FROM usage_ledger")).one()
    assert row.run_id is None and row.operation == "product.search" and row.credits == 1

    no_cat = _campaign(client, _product(client, category=None), {"keywords": ["x"]}, confirm=False)
    r = client.post(f"/api/v1/campaign-markets/{no_cat}/competitor-suggestions", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "need_category_or_keywords"
