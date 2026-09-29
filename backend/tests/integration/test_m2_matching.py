"""M2 验收：推荐卡五类信息；幂等不重复调用；费用上限停止；英国传 GB；IE 只能人工导入；币种为空显示未知。

FastMoss 用假客户端（FixtureAdapter），大模型用假函数，不消耗任何额度。
"""

import json
import uuid

import pytest
from sqlalchemy import text

from tk_workspace.integrations.fastmoss.mcp_client import Charge, FastMossInsufficientCredits, ToolResult
from tk_workspace.modules.matching import assess, pipeline, provider
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.jobs.runner import JobRunner, wait_until

FACTS = {
    "summary": "便携榨汁杯",
    "selling_points": ["便携"],
    "use_scenarios": ["办公室"],
    "forbidden_claims": [],
}
HARD_FOLLOWERS = {
    "field_key": "follower_count",
    "operator": "between",
    "expected": {"min": 10000, "max": 500000},
    "hardness": "hard",
}
SOFT_GMV = {"field_key": "day28_gmv", "operator": "gte", "expected": "1000", "hardness": "soft"}


def creator(n, followers=50000, gmv=3000, units=150, currency="GBP", er=4.2, **extra):
    rec = {
        "creator": {
            "uid": f"uid{n}",
            "unique_id": f"creator{n}",
            "nickname": f"Creator {n}",
            "region": "GB",
            "follower_count": followers,
            "is_ecommerce_creator": True,
            "signature": "Kitchen gadgets & smoothies",
        },
        "commerce_summary": {
            "day28_gmv": gmv,
            "day28_units_sold": units,
            "currency": currency,
            "total_units_sold": extra.pop("total_units_sold", 1000),
            "video_units_sold": extra.pop("video_units_sold", 800),
        },
        "audience_summary": {"engagement_rate_percent": er},
        "has_email": True,
    }
    for k, v in extra.items():
        if v is None:
            for part in rec.values():
                if isinstance(part, dict):
                    part.pop(k, None)
    return rec


class FakeFastMoss:
    """按页返回记录；记录每次调用参数；可模拟额度不足。"""

    def __init__(self, pages, total=None, charge=1, fail_on_call=None):
        self.pages, self.total, self.charge, self.fail_on_call = pages, total, charge, fail_on_call
        self.calls = []

    def __call__(self):
        return self

    def call_tool(self, name, arguments=None):
        self.calls.append((name, json.loads(json.dumps(arguments))))
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            raise FastMossInsufficientCredits("insufficient credits (402)")
        page = arguments.get("page", 1)
        recs = self.pages[page - 1] if page <= len(self.pages) else []
        total = self.total if self.total is not None else sum(len(p) for p in self.pages)
        charged = bool(recs)
        return ToolResult(
            tool=name,
            data={"total": total, "list": recs},
            charge=Charge(charged=charged, credit_cost=self.charge if charged else 0, remaining_credits=80),
            request_id=f"req-{len(self.calls)}",
        )

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(fm):
        provider.set_client_factory(fm)
        holder["fm"] = fm
        return fm

    monkeypatch.setattr(provider.limiter, "interval", 0)
    monkeypatch.setattr(assess, "call_model", _fake_model)
    yield install
    provider.set_client_factory(None)


def _fake_model(payload):
    out = []
    for c in payload["candidates"]:
        ids = [e["id"] for e in c["evidence"]]
        out.append(
            assess.CandidateJudgement(
                candidate_ref=c["ref"],
                product_fit="high",
                fit_points=[
                    assess.Point(text="内容以厨房小家电为主，与榨汁杯场景一致", evidence_ids=[ids[-1]])
                ],
                concerns=[assess.Point(text="编造的顾虑", evidence_ids=["E999"])],
                questions=["是否接受免费寄样？"],
                summary="厨房类内容，适合",
            )
        )
    return assess.BatchJudgement(candidates=out), {"model": "fake", "input_tokens": 100, "output_tokens": 50}


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


def _setup(client, market="UK", terms=None, criteria=(HARD_FOLLOWERS, SOFT_GMV), cap=10, target=5):
    p = client.post("/api/v1/products", json={"sku": f"SKU-{uuid.uuid4().hex[:6]}", "name": "榨汁杯"}).json()
    terms = terms or [
        {"market_code": market, "price_status": "known", "price_amount": "19.99", "sample_policy": "free"}
    ]
    assert (
        client.put(
            f"/api/v1/products/{p['id']}/draft", json={"facts": FACTS, "market_terms": terms}
        ).status_code
        == 200
    )
    assert client.post(f"/api/v1/products/{p['id']}/draft/confirm").status_code == 200
    c = client.post(
        "/api/v1/campaigns",
        json={
            "product_id": p["id"],
            "name": "t",
            "goal": "sales",
            "collaboration_type": "free_sample",
            "market_code": market,
            "market": {},
        },
    ).json()
    cm_id = c["markets"][0]["id"]
    client.put(
        f"/api/v1/campaign-markets/{cm_id}", json={"target_list_size": target, "cost_cap_credits": cap}
    )
    r = client.put(
        f"/api/v1/campaign-markets/{cm_id}/criteria",
        json={"criteria": list(criteria), "search": {"keywords": ["blender"]}},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/campaign-markets/{cm_id}/confirm")
    assert r.status_code == 200, r.text
    return cm_id


def _start(client, cm_id, key=None):
    return client.post(
        f"/api/v1/campaign-markets/{cm_id}/matching-runs",
        headers={"Idempotency-Key": key or f"k-{uuid.uuid4()}"},
    )


def _wait(client, run_id):
    assert wait_until(
        lambda: client.get(f"/api/v1/matching-runs/{run_id}").json()["status"] not in ("queued", "running"),
        timeout=20,
    ), client.get(f"/api/v1/matching-runs/{run_id}").json()
    return client.get(f"/api/v1/matching-runs/{run_id}/recommendations").json()


def _card(rec, handle):
    return next(c for c in rec["cards"] if c["creator"]["unique_id"] == handle)


# ---------------- 推荐卡 ----------------


def test_uk_run_sends_gb_and_card_shows_all_five_sections(client, runner, fake):
    fm = fake(
        FakeFastMoss(
            [
                [
                    creator(1),  # 合格
                    creator(2, followers=5000),  # 粉丝数不满足硬条件 → 排除
                    creator(3, gmv=970000, units=10),  # GMV 异常
                    creator(4, er=0, total_units_sold=0, video_units_sold=30),  # 互动率 0 → 未知；销量矛盾
                    creator(5, follower_count=None),  # 粉丝数缺失 → 待核实
                ]
            ]
        )
    )
    cm_id = _setup(client)
    r = _start(client, cm_id)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["provider_region"] == "GB" and run["reporting_currency"] == "GBP"
    rec = _wait(client, run["id"])
    assert rec["run"]["status"] == "succeeded", rec["run"]

    # 英国任务实际传 GB，硬条件粉丝数编进搜索参数
    name, args = fm.calls[0]
    assert name == "creator_search" and args["filter"]["region"] == "GB"
    assert args["filter"]["follower_range"] == {"min": 10000, "max": 500000}
    assert args["pagesize"] == 10 and args["keywords"] == "blender"

    counts = rec["snapshot"]["counts"]
    assert (counts["qualified"], counts["needs_verification"], counts["excluded"]) == (3, 1, 1)

    c1 = _card(rec, "creator1")
    assert c1["group"] == "qualified" and c1["creator"]["profile_url"] == "https://www.tiktok.com/@creator1"
    assert any(m["source"] == "rule" and "粉丝数" in m["text"] for m in c1["matches"])
    assert any(m["source"] == "ai" and m["evidence"] for m in c1["matches"])  # AI 匹配点带证据
    assert c1["ai"]["unsupported"] == ["编造的顾虑"]  # 引用不存在的证据 → 移为模型推断
    assert not any(m["source"] == "ai" for m in c1["mismatches"])
    assert any("带货资格" in u["text"] for u in c1["unknowns"])  # 带货资格默认未知
    assert any("内容语言" in u["text"] for u in c1["unknowns"])
    assert "是否接受免费寄样？" in c1["questions"]
    assert c1["metrics"]["day28_gmv"] == {
        "label": "近 28 天带货 GMV",
        "value": "3000",
        "state": "known",
        "currency": "GBP",
        "note": "",
    }

    c2 = _card(rec, "creator2")
    assert c2["group"] == "excluded" and any("粉丝数" in m["text"] for m in c2["mismatches"])

    c3 = _card(rec, "creator3")
    assert c3["metrics"]["day28_gmv"]["state"] == "anomaly"
    assert any("超出合理范围" in a["text"] for a in c3["anomalies"])
    assert any(u["text"].startswith("近 28 天带货 GMV：未知") for u in c3["unknowns"])  # 异常值不参与判断

    c4 = _card(rec, "creator4")
    assert c4["metrics"]["engagement_rate"]["state"] == "unknown"  # 0 不当作真实的 0
    assert any("互相矛盾" in a["text"] for a in c4["anomalies"])

    c5 = _card(rec, "creator5")
    assert c5["group"] == "needs_verification" and c5["hard_status"] == "unknown"
    assert c5["metrics"]["follower_count"]["value"] is None
    assert any("粉丝数" in q for q in c5["questions"])

    # 费用记账
    with tx() as s:
        ledger = s.execute(text("SELECT source, credits, input_tokens FROM usage_ledger")).all()
    assert sum(x.credits or 0 for x in ledger if x.source == "fastmoss") == 1 == rec["run"]["credits_used"]
    assert any(x.source == "llm" and x.input_tokens == 100 for x in ledger)


def test_unknown_with_exclude_policy_is_excluded(client, runner, fake):
    fake(FakeFastMoss([[creator(1, follower_count=None)]]))
    crit = [{**HARD_FOLLOWERS, "unknown_policy": "exclude"}]
    cm_id = _setup(client, criteria=crit)
    rec = _wait(client, _start(client, cm_id).json()["id"])
    assert _card(rec, "creator1")["group"] == "excluded"


# ---------------- 幂等与恢复 ----------------


def test_same_request_twice_does_not_call_provider_twice(client, runner, fake):
    fm = fake(FakeFastMoss([[creator(i) for i in range(10)], [creator(i) for i in range(10, 13)]]))
    cm_id = _setup(client)
    r1 = _start(client, cm_id, key="same-key-123")
    r2 = _start(client, cm_id, key="same-key-123")
    assert r1.status_code == 202 and r2.status_code == 200
    assert r2.headers["Idempotent-Replayed"] == "true" and r1.json()["id"] == r2.json()["id"]
    run_id = r1.json()["id"]
    rec = _wait(client, run_id)
    n_calls = len(fm.calls)
    assert n_calls == 2

    # 模拟中断后重跑同一运行：已完成的调用全部复用，快照不重复写
    pipeline.run_matching(None, {"run_id": run_id})
    assert len(fm.calls) == n_calls
    with tx() as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM recommendation_snapshots WHERE run_id=:r"), {"r": run_id}
            ).scalar()
            == 1
        )
        assert (
            s.execute(text("SELECT count(*) FROM provider_calls WHERE run_id=:r"), {"r": run_id}).scalar()
            == 2
        )
    assert (
        client.get(f"/api/v1/matching-runs/{run_id}").json()["credits_used"]
        == rec["run"]["credits_used"]
        == 2
    )


def test_interrupted_call_is_not_resent(client, runner, fake):
    fm = fake(FakeFastMoss([[creator(1)]]))
    cm_id = _setup(client)
    rec = _wait(client, _start(client, cm_id).json()["id"])
    run_id = rec["run"]["id"]
    with tx() as s:  # 假装调用发出后应用崩溃：记录停在 pending，快照没写
        s.execute(text("UPDATE provider_calls SET status='pending' WHERE run_id=:r"), {"r": run_id})
        s.execute(text("DELETE FROM recommendation_items"))
        s.execute(text("DELETE FROM recommendation_snapshots"))
    before = len(fm.calls)
    pipeline.run_matching(None, {"run_id": run_id})
    assert len(fm.calls) == before
    run = client.get(f"/api/v1/matching-runs/{run_id}").json()
    assert run["stop_reason"] == "provider_outcome_unknown" and run["status"] == "partial"
    assert run["calls"][0]["status"] == "unknown"


def test_only_one_active_run_and_requires_confirmation(client, fake):
    fake(FakeFastMoss([[creator(1)]]))
    cm_id = _setup(client)  # 没有启动执行器：运行一直排队
    assert _start(client, cm_id).status_code == 202
    r = _start(client, cm_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_in_progress"
    client.put(f"/api/v1/campaign-markets/{cm_id}", json={"target_list_size": 8, "cost_cap_credits": 10})
    r = _start(client, cm_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_confirmed"


# ---------------- 费用 ----------------


def test_stops_at_cost_cap(client, runner, fake):
    fm = fake(FakeFastMoss([[creator(p * 10 + i) for i in range(10)] for p in range(10)], total=2000))
    cm_id = _setup(client, cap=3, target=30)
    rec = _wait(client, _start(client, cm_id).json()["id"])
    run = rec["run"]
    assert len(fm.calls) == 3 and run["credits_used"] == 3
    assert run["counters"]["credits_used"] == 3  # 进度计数与实际扣费一致
    assert run["stop_reason"] == "cost_cap_reached" and run["status"] == "partial"
    assert any("额度上限" in x for x in rec["snapshot"]["limitations"])
    assert rec["snapshot"]["counts"]["total"] == 30


def test_insufficient_credits_stops_without_retry(client, runner, fake):
    fm = fake(FakeFastMoss([[creator(i) for i in range(10)]] * 5, total=2000, fail_on_call=2))
    cm_id = _setup(client, target=30)
    run = _wait(client, _start(client, cm_id).json()["id"])["run"]
    assert len(fm.calls) == 2 and run["stop_reason"] == "insufficient_credits" and run["status"] == "partial"
    assert [c["status"] for c in run["calls"]] == ["succeeded", "insufficient_credits"]


def test_cancel_queued_run(client, fake):
    fake(FakeFastMoss([[creator(1)]]))
    cm_id = _setup(client)
    run_id = _start(client, cm_id).json()["id"]
    r = client.post(f"/api/v1/matching-runs/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _start(client, cm_id).status_code == 202  # 取消后可以重新启动


# ---------------- 站点 ----------------


def test_ie_only_allows_manual_import(client, runner, fake):
    fm = fake(FakeFastMoss([[creator(1)]]))
    cm_id = _setup(
        client,
        market="IE",
        terms=[{"market_code": "IE", "price_status": "known", "price_amount": "22", "sample_policy": "free"}],
    )
    r = _start(client, cm_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "manual_import_only"
    assert fm.calls == []

    csv_text = "用户名,昵称,粉丝数,近28天GMV,币种\n@irish_cook,Irish Cook,82000,1500,EUR\nbad_row,,abc,,\n"
    r = client.post(
        f"/api/v1/campaign-markets/{cm_id}/manual-import",
        json={"csv": csv_text, "filename": "ie.csv"},
        headers={"Idempotency-Key": "import-key-1"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["source"] == "manual_import" and r.json()["provider_region"] is None
    rec = _wait(client, r.json()["id"])
    assert fm.calls == []  # 人工导入不调用 FastMoss
    card = _card(rec, "irish_cook")
    assert card["group"] == "qualified" and card["metrics"]["day28_gmv"]["currency"] == "EUR"
    bad = _card(rec, "bad_row")
    assert bad["group"] == "needs_verification" and bad["metrics"]["follower_count"]["value"] is None
    assert rec["snapshot"]["counts"]["total"] == 2


def test_market_without_currency_shows_currency_unknown(client, runner, fake):
    fake(FakeFastMoss([[creator(1, currency=None)]]))
    cm_id = _setup(
        client,
        market="NL",
        terms=[{"market_code": "NL", "price_status": "known", "price_amount": "21", "sample_policy": "free"}],
    )
    run = _start(client, cm_id).json()
    assert run["provider_region"] == "NL" and run["reporting_currency"] == "EUR"
    rec = _wait(client, run["id"])
    c = _card(rec, "creator1")
    gmv = c["metrics"]["day28_gmv"]
    assert gmv["value"] == "3000" and gmv["currency"] is None and gmv["note"] == "币种未知"
    soft = next(u for u in c["unknowns"] if "GMV" in u["text"] and u["tag"] == "软条件")
    assert "币种未知" in soft["text"]  # 不默认当作 EUR
    assert any("币种未知" in x for x in rec["snapshot"]["limitations"])


# ---------------- AI ----------------


def test_llm_failure_marks_candidates_and_run_partial(client, runner, fake, monkeypatch):
    fake(FakeFastMoss([[creator(1), creator(2)]]))

    def boom(payload):
        raise ValueError("bad json")

    monkeypatch.setattr(assess, "call_model", boom)
    cm_id = _setup(client)
    rec = _wait(client, _start(client, cm_id).json()["id"])
    assert rec["run"]["status"] == "partial"
    assert all(c["ai"]["status"] == "failed" for c in rec["cards"])
    assert all(any(m["source"] == "rule" for m in c["matches"]) for c in rec["cards"])  # 规则判断仍在


def test_without_llm_config_cards_have_rule_results_only(client, runner, fake, monkeypatch):
    fake(FakeFastMoss([[creator(1)]]))
    monkeypatch.setattr(assess, "call_model", assess._default_call_model)
    cm_id = _setup(client)
    rec = _wait(client, _start(client, cm_id).json()["id"])
    assert rec["run"]["status"] == "succeeded"
    c = _card(rec, "creator1")
    assert c["ai"]["status"] == "skipped" and c["matches"]
    assert any("没有 AI 判断" in x for x in rec["snapshot"]["limitations"])
