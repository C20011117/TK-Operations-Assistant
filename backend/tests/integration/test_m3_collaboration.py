"""M3 合作与寄样：本人确认 + 幂等防重复、修改使确认失效、签收未知保持未知、地址加密且不进日志。"""

import logging
import uuid

import pytest
from sqlalchemy import text

from tk_workspace.config import get_settings
from tk_workspace.platform import crypto, secrets
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.jobs.runner import JobRunner, wait_until

FACTS = {
    "summary": "便携榨汁杯",
    "selling_points": ["便携"],
    "use_scenarios": ["办公室"],
    "forbidden_claims": [],
}
ADDRESS = "221B Baker Street Flat 7"
NAME = "Sherlock Holmesworth"
PHONE = "+44 7700 900123"
TRACKING = "RM123456789GB"
RECIPIENT = {
    "name": NAME,
    "phone": PHONE,
    "email": "sherlock.h@example.co.uk",
    "address_line1": ADDRESS,
    "city": "London",
    "postcode": "NW1 6XE",
    "country_code": "gb",
}
SECRETS = (ADDRESS, NAME, PHONE, TRACKING, "sherlock.h@example.co.uk", "NW1 6XE")


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


def _key() -> dict:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def _setup(client):
    """产品 + 英国任务 + 人工导入两位候选（不调用 FastMoss、不需要大模型）。"""
    p = client.post("/api/v1/products", json={"sku": f"SKU-{uuid.uuid4().hex[:6]}", "name": "榨汁杯"}).json()
    terms = [{"market_code": "UK", "price_status": "known", "price_amount": "19.99", "sample_policy": "free"}]
    client.put(f"/api/v1/products/{p['id']}/draft", json={"facts": FACTS, "market_terms": terms})
    client.post(f"/api/v1/products/{p['id']}/draft/confirm")
    c = client.post(
        "/api/v1/campaigns",
        json={
            "product_id": p["id"],
            "name": "榨汁杯 UK",
            "goal": "sales",
            "collaboration_type": "free_sample",
            "market_code": "UK",
            "market": {},
        },
    ).json()
    cm_id = c["markets"][0]["id"]
    client.put(f"/api/v1/campaign-markets/{cm_id}", json={"target_list_size": 5, "cost_cap_credits": 10})
    crit = [
        {
            "field_key": "follower_count",
            "operator": "between",
            "expected": {"min": 1000, "max": 900000},
            "hardness": "hard",
        }
    ]
    client.put(
        f"/api/v1/campaign-markets/{cm_id}/criteria", json={"criteria": crit, "search": {"keywords": ["x"]}}
    )
    assert client.post(f"/api/v1/campaign-markets/{cm_id}/confirm").status_code == 200
    csv_text = "用户名,昵称,粉丝数,近28天GMV,币种\n@kitchen_kate,Kate,82000,1500,GBP\n@gadget_gus,Gus,50000,900,GBP\n"
    r = client.post(f"/api/v1/campaign-markets/{cm_id}/manual-import", json={"csv": csv_text}, headers=_key())
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert wait_until(
        lambda: client.get(f"/api/v1/matching-runs/{run_id}").json()["status"] not in ("queued", "running"),
        timeout=20,
    )
    cards = client.get(f"/api/v1/matching-runs/{run_id}/recommendations").json()["cards"]
    by = {c["creator"]["unique_id"]: c for c in cards}
    return cm_id, run_id, by


def _keep_and_collab(client, cm_id, run_id, card):
    r = client.post(
        f"/api/v1/campaign-markets/{cm_id}/creator-decisions",
        json={
            "creator_id": card["creator"]["id"],
            "decision": "keep",
            "reason_code": "good_fit",
            "run_id": run_id,
            "evaluation_id": card["evaluation_id"],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/v1/campaign-markets/{cm_id}/collaborations",
        json={"creator_id": card["creator"]["id"], "run_id": run_id, "evaluation_id": card["evaluation_id"]},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _agreed(client, runner):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/confirm-agreement",
        json={
            "agreed_via": "tiktok_message",
            "agreed_on": "2026-09-28",
            "agreed_video_count": 2,
            "terms_note": "免费寄样 1 台，两条新视频，佣金 15%",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _shipment_body(qty=1, recipient=True):
    body = {
        "kind": "initial_sample",
        "items": [{"sku": "JC-01", "variant": "粉色", "quantity": qty, "unit_cost": "6.50"}],
        "cost_cap_amount": "20",
        "note": "",
    }
    if recipient:
        body["recipient"] = RECIPIENT
    return body


def _new_shipment(client, co_id):
    r = client.post(f"/api/v1/collaborations/{co_id}/shipments", json=_shipment_body(), headers=_key())
    assert r.status_code == 201, r.text
    return r.json()


def _confirm(client, sid):
    pv = client.post(f"/api/v1/shipments/{sid}/confirmation-preview").json()
    r = client.post(
        f"/api/v1/shipments/{sid}/confirm",
        json={"payload_version": pv["payload_version"], "payload_hash": pv["payload_hash"]},
        headers=_key(),
    )
    assert r.status_code == 200, r.text
    return pv, r.json()


def _count(sql, **p):
    with tx() as s:
        return s.execute(text(sql), p).scalar()


# ---------------- 候选决定与合作 ----------------


def test_decisions_are_appended_and_collaboration_needs_keep(client, runner):
    cm_id, run_id, by = _setup(client)
    gus = by["gadget_gus"]
    url = f"/api/v1/campaign-markets/{cm_id}/creator-decisions"
    assert (
        client.post(
            url, json={"creator_id": gus["creator"]["id"], "decision": "needs_verification"}
        ).status_code
        == 201
    )
    r = client.post(
        f"/api/v1/campaign-markets/{cm_id}/collaborations", json={"creator_id": gus["creator"]["id"]}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "decision_required"
    client.post(
        url, json={"creator_id": gus["creator"]["id"], "decision": "exclude", "reason_code": "off_target"}
    )
    current = {d["creator_id"]: d for d in client.get(url).json()}
    assert current[gus["creator"]["id"]]["decision"] == "exclude"
    assert _count("SELECT COUNT(*) FROM creator_decisions WHERE creator_id=:c", c=gus["creator"]["id"]) == 2

    # 评价引用必须属于这个达人
    r = client.post(
        url,
        json={
            "creator_id": gus["creator"]["id"],
            "decision": "keep",
            "evaluation_id": by["kitchen_kate"]["evaluation_id"],
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_reference"

    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    assert co["status"] == "planned" and co["next_step"] == "联系达人"
    again = client.post(
        f"/api/v1/campaign-markets/{cm_id}/collaborations",
        json={"creator_id": by["kitchen_kate"]["creator"]["id"]},
    )
    assert again.status_code == 200 and again.json()["id"] == co["id"]  # 不重复建合作
    current = {d["creator_id"]: d for d in client.get(url).json()}
    assert current[by["kitchen_kate"]["creator"]["id"]]["collaboration_id"] == co["id"]


def test_status_flow_agreement_required_before_shipping(client, runner):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    cid = co["id"]
    r = client.post(f"/api/v1/collaborations/{cid}/shipments", json=_shipment_body(), headers=_key())
    assert r.status_code == 409 and r.json()["error"]["code"] == "agreement_required"
    # 匹配通过不能直接变成“已约定”
    assert client.post(f"/api/v1/collaborations/{cid}/transitions", json={"to": "agreed"}).status_code == 422
    r = client.post(
        f"/api/v1/collaborations/{cid}/transitions", json={"to": "contacting", "revision": co["revision"]}
    )
    assert r.status_code == 200 and r.json()["status"] == "contacting"
    stale = client.post(
        f"/api/v1/collaborations/{cid}/transitions", json={"to": "negotiating", "revision": co["revision"]}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"
    r = client.post(
        f"/api/v1/collaborations/{cid}/confirm-agreement",
        json={
            "agreed_via": "email",
            "agreed_on": "2026-09-28",
            "agreed_video_count": 2,
            "terms_note": "两条视频",
        },
    )
    body = r.json()
    assert body["status"] == "agreed" and body["agreement"]["agreed_video_count"] == 2
    assert body["can_create_shipment"] and body["next_step"] == "创建寄样单"
    assert [e["to_status"] for e in body["events"] if e["to_status"]] == ["planned", "contacting", "agreed"]
    assert (
        client.post(
            f"/api/v1/collaborations/{cid}/confirm-agreement",
            json={
                "agreed_via": "email",
                "agreed_on": "2026-09-28",
                "agreed_video_count": 1,
                "terms_note": "x",
            },
        ).status_code
        == 409
    )


# ---------------- 寄样：确认、幂等、防重复 ----------------


def test_shipment_confirm_dispatch_idempotent_no_duplicates(client, runner):
    co = _agreed(client, runner)
    cid = co["id"]
    key = _key()
    r1 = client.post(f"/api/v1/collaborations/{cid}/shipments", json=_shipment_body(), headers=key)
    r2 = client.post(f"/api/v1/collaborations/{cid}/shipments", json=_shipment_body(), headers=key)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    r3 = client.post(f"/api/v1/collaborations/{cid}/shipments", json=_shipment_body(qty=2), headers=key)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_conflict"
    ship = r1.json()
    sid = ship["id"]
    assert ship["status"] == "draft" and ship["next_action"] == "preview"
    assert ship["recipient_masked"] == "S*** · NW1 *** · GB" and ship["items_cost_total"] == "6.5"
    assert ship["currency"] == "GBP"

    # 没核对不能确认；哈希不对不能确认
    bad = client.post(
        f"/api/v1/shipments/{sid}/confirm",
        json={"payload_version": 1, "payload_hash": "0" * 64},
        headers=_key(),
    )
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "not_previewed"
    pv = client.post(f"/api/v1/shipments/{sid}/confirmation-preview").json()
    assert pv["summary"]["recipient"] == "S*** · NW1 *** · GB" and NAME not in str(pv)
    bad = client.post(
        f"/api/v1/shipments/{sid}/confirm",
        json={"payload_version": 1, "payload_hash": "0" * 64},
        headers=_key(),
    )
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "payload_changed"
    # 没确认不能登记寄出
    d = client.post(
        f"/api/v1/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=_key()
    )
    assert d.status_code == 409 and d.json()["error"]["code"] == "not_confirmed"

    ck = _key()
    body = {"payload_version": pv["payload_version"], "payload_hash": pv["payload_hash"]}
    c1 = client.post(f"/api/v1/shipments/{sid}/confirm", json=body, headers=ck)
    c2 = client.post(f"/api/v1/shipments/{sid}/confirm", json=body, headers=ck)
    c3 = client.post(f"/api/v1/shipments/{sid}/confirm", json=body, headers=_key())
    assert c1.status_code == 200 and c1.json()["status"] == "confirmed" and c1.json()["confirmation"]["valid"]
    assert c2.status_code == 200 and c2.headers.get("Idempotent-Replayed") == "true"
    assert c3.status_code == 409 and c3.json()["error"]["code"] == "already_confirmed"
    assert _count("SELECT COUNT(*) FROM action_confirmations") == 1

    dk = _key()
    dbody = {"dispatched_at": "2026-09-29T09:30:00Z", "carrier": "Royal Mail", "tracking_number": TRACKING}
    d1 = client.post(f"/api/v1/shipments/{sid}/register-dispatch", json=dbody, headers=dk)
    d2 = client.post(f"/api/v1/shipments/{sid}/register-dispatch", json=dbody, headers=dk)
    d3 = client.post(f"/api/v1/shipments/{sid}/register-dispatch", json=dbody, headers=_key())
    assert d1.status_code == 200, d1.text
    v = d1.json()
    assert v["status"] == "dispatched" and v["delivery_status"] == "unknown" and v["delivered_at"] is None
    assert v["tracking_masked"] == "***89GB" and v["action_status"] == "succeeded"
    assert v["confirmation"]["consumed_at"] and not v["confirmation"]["valid"]
    assert d2.status_code == 200 and d2.headers.get("Idempotent-Replayed") == "true"
    assert d3.status_code == 409 and d3.json()["error"]["code"] == "already_dispatched"
    assert _count("SELECT COUNT(*) FROM external_actions WHERE status='succeeded'") == 1
    assert _count("SELECT COUNT(*) FROM sample_shipments") == 1
    # 寄出后不能再改、不能取消
    assert client.put(f"/api/v1/shipments/{sid}", json=_shipment_body(recipient=False)).status_code == 409
    assert client.post(f"/api/v1/shipments/{sid}/cancel").status_code == 409

    detail = client.get(f"/api/v1/collaborations/{cid}").json()
    assert detail["status"] == "in_progress" and detail["delivery_status"] == "unknown"
    assert detail["next_step"] == "跟进物流，登记签收"


def test_edit_after_confirm_invalidates_confirmation(client, runner):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    old_pv, _ = _confirm(client, sid)
    r = client.put(f"/api/v1/shipments/{sid}", json=_shipment_body(qty=2, recipient=False))
    v = r.json()
    assert r.status_code == 200 and v["status"] == "draft" and v["payload_version"] == 2
    assert v["confirmation"] is None and v["recipient_masked"] == "S*** · NW1 *** · GB"  # 收件信息沿用
    assert _count("SELECT COUNT(*) FROM action_confirmations WHERE revoked_at IS NOT NULL") == 1
    d = client.post(
        f"/api/v1/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=_key()
    )
    assert d.status_code == 409 and d.json()["error"]["code"] == "not_confirmed"
    new_pv = client.post(f"/api/v1/shipments/{sid}/confirmation-preview").json()
    assert new_pv["payload_hash"] != old_pv["payload_hash"] and new_pv["summary"]["items"][0]["quantity"] == 2
    stale = client.post(
        f"/api/v1/shipments/{sid}/confirm",
        json={"payload_version": old_pv["payload_version"], "payload_hash": old_pv["payload_hash"]},
        headers=_key(),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "payload_changed"
    # 改收件地址同样使确认失效
    _, confirmed = _confirm(client, sid)
    assert confirmed["payload_version"] == 2
    moved = {**_shipment_body(qty=2), "recipient": {**RECIPIENT, "address_line1": "10 Downing Street"}}
    v = client.put(f"/api/v1/shipments/{sid}", json=moved).json()
    assert v["status"] == "draft" and v["payload_version"] == 3


def test_expired_confirmation_requires_reconfirm(client, runner):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    _confirm(client, sid)
    with tx() as s:
        s.execute(text("UPDATE action_confirmations SET expires_at='2026-01-01T00:00:00.000Z'"))
    d = client.post(
        f"/api/v1/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=_key()
    )
    assert d.status_code == 409 and d.json()["error"]["code"] == "confirmation_expired"
    assert client.get(f"/api/v1/shipments/{sid}").json()["status"] == "draft"


def test_closing_collaboration_cancels_pending_shipment(client, runner):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    _confirm(client, sid)
    r = client.post(f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "closed"})
    assert r.status_code == 422  # 关闭需要原因
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "closed", "closed_reason": "declined"}
    )
    assert r.status_code == 200 and r.json()["status"] == "closed"
    v = client.get(f"/api/v1/shipments/{sid}").json()
    assert v["status"] == "cancelled" and v["confirmation"] is None
    assert _count("SELECT status FROM external_actions") == "cancelled"


# ---------------- 签收 ----------------


def test_delivery_unknown_until_event_and_projection_by_occurred_time(client, runner):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    r = client.post(
        f"/api/v1/shipments/{sid}/events", json={"status": "delivered", "occurred_at": "2026-09-29"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_dispatched"
    _confirm(client, sid)
    client.post(
        f"/api/v1/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-20"}, headers=_key()
    )
    v = client.get(f"/api/v1/shipments/{sid}").json()
    assert v["delivery_status"] == "unknown" and v["next_action"] == "record_delivery"
    early = client.post(
        f"/api/v1/shipments/{sid}/events", json={"status": "in_transit", "occurred_at": "2026-09-10"}
    )
    assert early.status_code == 422
    client.post(
        f"/api/v1/shipments/{sid}/events", json={"status": "delivered", "occurred_at": "2026-09-24T15:00:00Z"}
    )
    # 晚登记的“运输中”发生得更早：不能覆盖已签收
    v = client.post(
        f"/api/v1/shipments/{sid}/events", json={"status": "in_transit", "occurred_at": "2026-09-22"}
    ).json()
    assert v["delivery_status"] == "delivered" and v["delivered_at"].startswith("2026-09-24")
    assert [e["status"] for e in v["events"]] == ["in_transit", "delivered"] and v["next_action"] is None
    future = client.post(
        f"/api/v1/shipments/{sid}/events", json={"status": "returned", "occurred_at": "2030-01-01"}
    )
    assert future.status_code == 422


# ---------------- 个人数据 ----------------


def test_address_encrypted_at_rest_and_bound_to_row(client, runner):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    _confirm(client, sid)
    client.post(
        f"/api/v1/shipments/{sid}/register-dispatch",
        json={"dispatched_at": "2026-09-29", "tracking_number": TRACKING},
        headers=_key(),
    )
    sid2 = _new_shipment(client, co["id"])["id"]
    with tx() as s:
        s.execute(text("PRAGMA wal_checkpoint(FULL)"))
        row = s.execute(
            text("SELECT recipient_enc, tracking_enc FROM sample_shipments WHERE id=:id"), {"id": sid}
        ).first()
    assert row.recipient_enc.startswith("v1:") and row.tracking_enc.startswith("v1:")
    db = get_settings().db_path
    raw = b"".join(p.read_bytes() for p in db.parent.glob(db.name + "*"))
    for secret in SECRETS:
        assert secret.encode() not in raw, secret

    full = client.get(f"/api/v1/shipments/{sid}/recipient").json()
    assert full["recipient"]["address_line1"] == ADDRESS and full["tracking_number"] == TRACKING
    assert full["recipient"]["country_code"] == "GB"

    # 密文挪到别的行：附加数据不匹配，解密失败
    with tx() as s:
        s.execute(
            text("UPDATE sample_shipments SET recipient_enc=:r WHERE id=:id"),
            {"r": row.recipient_enc, "id": sid2},
        )
    r = client.get(f"/api/v1/shipments/{sid2}/recipient")
    assert r.status_code == 409 and r.json()["error"]["code"] == "pii_key_unavailable"


def test_other_machine_without_key_cannot_read(client, runner, memory_keyring):
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    # 模拟把数据库拷到别的电脑：凭据管理器里没有这把数据密钥
    memory_keyring.store.clear()
    r = client.get(f"/api/v1/shipments/{sid}/recipient")
    assert r.status_code == 409 and r.json()["error"]["code"] == "pii_key_unavailable"
    assert client.get(f"/api/v1/shipments/{sid}").json()["recipient_masked"] == "（本机无法解密收件信息）"
    assert secrets.get_secret(secrets.PII_DATA_KEY) == ""  # 读取不会偷偷生成新密钥


def test_pii_never_in_logs_or_error_responses(client, runner, caplog):
    caplog.set_level(logging.DEBUG)
    co = _agreed(client, runner)
    bad = client.post(
        f"/api/v1/collaborations/{co['id']}/shipments",
        json={**_shipment_body(), "recipient": {**RECIPIENT, "country_code": "GBR"}},
        headers=_key(),
    )
    assert bad.status_code == 422 and ADDRESS not in bad.text and NAME not in bad.text
    sid = _new_shipment(client, co["id"])["id"]
    _confirm(client, sid)
    client.post(
        f"/api/v1/shipments/{sid}/register-dispatch",
        json={"dispatched_at": "2026-09-29", "tracking_number": TRACKING},
        headers=_key(),
    )
    client.get(f"/api/v1/shipments/{sid}/recipient")
    for secret in SECRETS:
        assert secret not in caplog.text, secret
    log_dir = get_settings().logs_dir
    for f in log_dir.glob("*"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        assert all(sec not in content for sec in SECRETS)
    # 确认摘要和对外动作记录里也只有脱敏信息
    with tx() as s:
        blobs = " ".join(
            str(v)
            for v in s.execute(
                text("SELECT summary FROM action_confirmations UNION ALL SELECT result FROM external_actions")
            ).scalars()
        )
    assert all(sec not in blobs for sec in SECRETS)


def test_crypto_roundtrip_and_keyed_hash():
    tok = crypto.encrypt({"a": 1}, table="t", column="c", row_id="r1")
    assert crypto.decrypt(tok, table="t", column="c", row_id="r1") == {"a": 1}
    with pytest.raises(crypto.PiiKeyError):
        crypto.decrypt(tok, table="t", column="c", row_id="r2")
    assert crypto.keyed_hash({"x": 1, "y": 2}) == crypto.keyed_hash({"y": 2, "x": 1})
    assert len(crypto.keyed_hash({"x": 1})) == 64
