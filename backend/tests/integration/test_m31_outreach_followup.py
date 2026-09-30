"""M3.1：跟进提醒、邀约话术。

- 跟进提醒按“阶段 + 最近一次进展”计算；到期才出现在“待跟进”；有新进展或手动跟进后重新计时。
- 话术只生成草稿；发给模型的内容不含收件信息；禁用表述会被标出；标记已发送会推进状态并记一次进展。
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tk_workspace.modules.collaborations import outreach
from tk_workspace.modules.collaborations.outreach import DraftOut
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.jobs.runner import JobRunner

from .test_m3_collaboration import (
    SECRETS,
    _agreed,
    _confirm,
    _count,
    _keep_and_collab,
    _key,
    _new_shipment,
    _setup,
)


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


def _ago(days: float) -> str:
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _age_events(co_id: str, days: float) -> None:
    """把这个合作的所有进展往前挪 N 天（模拟时间流逝）。"""
    with tx() as s:
        s.execute(
            text("UPDATE collaboration_events SET created_at=:t WHERE collaboration_id=:c"),
            {"t": _ago(days), "c": co_id},
        )


def _get(client, co_id):
    return client.get(f"/api/v1/collaborations/{co_id}").json()


def _due_ids(client):
    r = client.get("/api/v1/collaborations", params={"due": "true"})
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()]


# ---------------- 跟进提醒 ----------------


def test_follow_up_due_by_stage_and_reset_by_progress(client, runner):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    fu = _get(client, co["id"])["follow_up"]
    assert fu["overdue"] is False and fu["source"] == "rule"
    assert co["id"] not in _due_ids(client)

    # 准备联系 3 天没动 → 到期
    _age_events(co["id"], 3)
    fu = _get(client, co["id"])["follow_up"]
    assert fu["overdue"] is True and "准备联系" in fu["message"] and fu["idle_days"] == 3
    assert co["id"] in _due_ids(client)

    # 有新进展（已联系）→ 重新计时，规则变为 3 天
    detail = _get(client, co["id"])
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions",
        json={"to": "contacting", "revision": detail["revision"]},
    )
    assert r.status_code == 200, r.text
    fu = r.json()["follow_up"]
    assert fu["overdue"] is False and fu["suggest_follow_up_draft"] is True
    _age_events(co["id"], 4)
    fu = _get(client, co["id"])["follow_up"]
    assert fu["overdue"] and "已联系" in fu["message"]

    # 记录一次跟进，5 天后再提醒 → 不再到期，时间为手动
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/follow-ups", json={"note": "再次私信", "next_in_days": 5}
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["follow_up"]["overdue"] is False and d["follow_up"]["source"] == "manual"
    assert d["events"][-1]["kind"] == "follow_up" and "再次私信" in d["events"][-1]["note"]
    assert co["id"] not in _due_ids(client)


def test_snooze_and_new_progress_clears_manual_time(client, runner):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    _age_events(co["id"], 5)
    assert co["id"] in _due_ids(client)

    r = client.post(f"/api/v1/collaborations/{co['id']}/snooze", json={"days": 3})
    assert r.status_code == 200, r.text
    fu = r.json()["follow_up"]
    assert fu["overdue"] is False and fu["source"] == "manual"
    # 稍后提醒不算进展：历史里没有新事件
    assert all(e["kind"] != "follow_up" for e in r.json()["events"])

    # 之后有了真正的进展：手动时间作废，按规则计时
    rev = r.json()["revision"]
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "contacting", "revision": rev}
    )
    assert r.json()["follow_up"]["source"] == "rule"


def test_follow_up_after_dispatch_and_closed(client, runner):
    co = _agreed(client, runner)
    s = _new_shipment(client, co["id"])
    _confirm(client, s["id"])
    r = client.post(
        f"/api/v1/shipments/{s['id']}/register-dispatch", json={"dispatched_at": "2026-09-20"}, headers=_key()
    )
    assert r.status_code == 200, r.text
    _age_events(co["id"], 8)
    fu = _get(client, co["id"])["follow_up"]
    assert fu["overdue"] and "寄出" in fu["message"]

    detail = _get(client, co["id"])
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions",
        json={"to": "closed", "closed_reason": "declined", "revision": detail["revision"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["follow_up"] is None
    assert co["id"] not in _due_ids(client)
    r = client.post(f"/api/v1/collaborations/{co['id']}/follow-ups", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_active"


# ---------------- 邀约话术 ----------------


@pytest.fixture
def fake_model(monkeypatch):
    calls: list[tuple[str, dict]] = []
    reply = {
        "value": DraftOut(
            subject="should be dropped for DM",
            message="Hi Kate! I'm the BD at {brand}. Our portable blender is perfect for busy mornings.",
            message_zh="嗨 Kate！我是 {brand} 的 BD。我们的便携榨汁杯很适合忙碌的早晨。",
            personalization=["达人内容偏厨房和健康饮食"],
            cautions=["确认是否可以免费寄样"],
        )
    }

    def fake(system, payload):
        calls.append((system, payload))
        if reply.get("fail"):
            raise TimeoutError("Request timed out.")
        return reply["value"], {"model": "fake-model", "input_tokens": 100, "output_tokens": 50}

    monkeypatch.setattr(outreach, "call_model", fake)
    return calls, reply


def test_draft_generated_without_pii_and_mark_sent_moves_to_contacting(client, runner, fake_model):
    calls, _ = fake_model
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])

    r = client.post(f"/api/v1/collaborations/{co['id']}/outreach-drafts", json={})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["purpose"] == "invite" and d["channel"] == "tiktok_message"
    assert d["language"] == "en"  # 英国站默认英语
    assert d["subject"] is None  # 私信没有标题
    assert any("{brand}" in w for w in d["warnings"])
    assert "确认是否可以免费寄样" in d["warnings"]
    system, payload = calls[-1]
    assert "700" in system
    assert payload["product"]["name"] and payload["terms"] != "unknown"
    assert payload["creator"]["handle"] == "kitchen_kate"
    assert _count("SELECT COUNT(*) FROM usage_ledger WHERE operation='outreach.draft'") == 1

    # 标记已发送：准备联系 → 联系中，历史里有记录
    r = client.post(f"/api/v1/outreach-drafts/{d['id']}/mark-sent", json={"note": "已私信"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["status"] == "contacting"
    assert "已通过TikTok 私信发送邀约消息" in detail["events"][-1]["note"]
    r = client.post(f"/api/v1/outreach-drafts/{d['id']}/mark-sent", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_sent"

    listed = client.get(f"/api/v1/collaborations/{co['id']}/outreach-drafts").json()
    assert len(listed) == 1 and listed[0]["sent_at"]

    # 跟进话术：带上一条已发送的消息和停滞天数；邮件有标题
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/outreach-drafts",
        json={"purpose": "follow_up", "channel": "email", "language": "de"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["subject"] and r.json()["language"] == "de"
    _, payload = calls[-1]
    assert payload["previous_message"].startswith("Hi Kate")
    assert "days_since_last_progress" in payload
    assert payload["language"].startswith("de")


def test_draft_never_contains_recipient_data(client, runner, fake_model):
    calls, _ = fake_model
    co = _agreed(client, runner)
    _new_shipment(client, co["id"])  # 已保存加密的收件信息
    r = client.post(f"/api/v1/collaborations/{co['id']}/outreach-drafts", json={"purpose": "follow_up"})
    assert r.status_code == 201, r.text
    dumped = json.dumps(calls[-1][1], ensure_ascii=False)
    assert all(sec not in dumped for sec in SECRETS)
    assert calls[-1][1]["agreement"]["agreed_video_count"] == 2


def test_forbidden_claims_flagged_and_llm_failure_is_readable(client, runner, fake_model):
    _, reply = fake_model
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    with tx() as s:
        s.execute(
            text(
                "UPDATE product_versions SET facts=json_set(facts, '$.forbidden_claims', json('[\"perfect\"]'))"
            )
        )
    r = client.post(f"/api/v1/collaborations/{co['id']}/outreach-drafts", json={"channel": "email"})
    assert r.status_code == 201, r.text
    assert any("禁用表述" in w and "perfect" in w for w in r.json()["warnings"])

    reply["fail"] = True
    r = client.post(f"/api/v1/collaborations/{co['id']}/outreach-drafts", json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_failed"
    # 失败不影响合作数据
    assert _get(client, co["id"])["status"] == "planned"


def test_draft_requires_llm_configured(client, runner):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    r = client.post(f"/api/v1/collaborations/{co['id']}/outreach-drafts", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "llm_not_configured"
