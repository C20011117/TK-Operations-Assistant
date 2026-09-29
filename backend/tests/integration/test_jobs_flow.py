"""M0 链路验收：API 幂等创建任务 → outbox → 分发器 → Worker 执行 → 状态可查；fencing 防止旧 worker 写回。"""

import uuid

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import TEST_PASSWORD
from tests.integration.helpers import ctx_of
from tk_workspace.api.main import create_app
from tk_workspace.platform.db.session import runtime_tx_sync
from tk_workspace.platform.jobs.executor import execute_job
from tk_workspace.platform.outbox import dispatcher

pytestmark = pytest.mark.integration


@pytest.fixture
async def bd_client(seeded):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": "bd.a@demo.local", "password": TEST_PASSWORD})
        me = r.json()
        c.headers["X-CSRF-Token"] = c.cookies.get("tkws_csrf")
        c.tenant_id = me["current"]["tenant_id"]  # type: ignore[attr-defined]
        yield c


def _drain_outbox() -> list[str]:
    sent: list[str] = []
    dispatcher.run_once(publish=lambda job_id, queue: sent.append(job_id))
    return sent


async def test_create_dispatch_execute(bd_client):
    key = f"test-{uuid.uuid4()}"
    url = f"/api/v1/tenants/{bd_client.tenant_id}/system/jobs"
    r = await bd_client.post(
        url,
        json={"kind": "system.noop", "params": {"sleep_ms": 10, "message": "hi"}},
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "queued"

    sent = _drain_outbox()
    assert job["id"] in sent
    # 再次分发不会重复投递同一事件
    assert job["id"] not in _drain_outbox()

    assert execute_job(job["id"], "test-worker") == "succeeded"
    # 至少一次投递：重复消息直接跳过
    assert execute_job(job["id"], "test-worker") == "skipped"

    r = await bd_client.get(f"{url}/{job['id']}")
    done = r.json()
    assert done["status"] == "succeeded"
    assert done["result"]["echo"] == "hi"
    assert done["progress"] == {"percent": 100}


async def test_idempotency_replay_and_conflict(bd_client):
    key = f"idem-{uuid.uuid4()}"
    url = f"/api/v1/tenants/{bd_client.tenant_id}/system/jobs"
    body = {"kind": "system.noop", "params": {"message": "once"}}
    r1 = await bd_client.post(url, json=body, headers={"Idempotency-Key": key})
    r2 = await bd_client.post(url, json=body, headers={"Idempotency-Key": key})
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.headers["Idempotent-Replayed"] == "true"
    r3 = await bd_client.post(
        url,
        json={"kind": "system.noop", "params": {"message": "different"}},
        headers={"Idempotency-Key": key},
    )
    assert r3.status_code == 409


async def test_failed_job_records_error(bd_client):
    url = f"/api/v1/tenants/{bd_client.tenant_id}/system/jobs"
    r = await bd_client.post(
        url,
        json={"kind": "system.noop", "params": {"sleep_ms": 0, "fail": True}},
        headers={"Idempotency-Key": f"fail-{uuid.uuid4()}"},
    )
    job_id = r.json()["id"]
    _drain_outbox()
    assert execute_job(job_id, "test-worker") == "failed"
    done = (await bd_client.get(f"{url}/{job_id}")).json()
    assert done["status"] == "failed"
    assert done["last_error"]["class"] == "RuntimeError"


def test_stale_fencing_token_cannot_write(seeded):
    a = ctx_of(seeded, "bd.a@demo.local")
    job_id = str(uuid.uuid4())
    with runtime_tx_sync(a) as s:
        s.execute(
            text(
                """INSERT INTO jobs (id, tenant_id, kind, owner_principal_id, requested_by, status, fencing_token,
                                     lease_expires_at)
                   VALUES (:id, :t, 'system.noop', :p, :p, 'running', 5, now() - interval '1 minute')"""
            ),
            {"id": job_id, "t": a["app.tenant_id"], "p": a["app.principal_id"]},
        )
    # 租约已过期 → 新 worker 接管（token 变为 6）并完成
    assert execute_job(job_id, "worker-new") == "succeeded"
    # 旧 worker 用 token 5 写回会被拒绝
    with runtime_tx_sync(a) as s:
        res = s.execute(
            text("UPDATE jobs SET status = 'failed' WHERE id = :id AND fencing_token = 5"), {"id": job_id}
        )
        assert res.rowcount == 0
