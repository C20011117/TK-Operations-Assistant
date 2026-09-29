"""任务链路：创建（幂等）→ 执行器领取 → 完成；取消；fencing；重启恢复。"""

import json
import uuid

import pytest
from sqlalchemy import text

from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs import executor
from tk_workspace.platform.jobs import service as jobs
from tk_workspace.platform.jobs.registry import JobKind, register
from tk_workspace.platform.jobs.runner import JobRunner, wait_until


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=2, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


def _key() -> str:
    return f"test-{uuid.uuid4()}"


def test_api_job_runs_to_completion(client, runner):
    r = client.post(
        "/api/v1/system/jobs",
        json={"kind": "system.noop", "params": {"sleep_ms": 100, "message": "hi"}},
        headers={"Idempotency-Key": _key()},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    assert wait_until(lambda: client.get(f"/api/v1/system/jobs/{job_id}").json()["status"] == "succeeded")
    job = client.get(f"/api/v1/system/jobs/{job_id}").json()
    assert job["result"]["echo"] == "hi" and job["progress"]["percent"] == 100 and job["attempt"] == 1


def test_idempotent_replay_and_conflict(client):
    key = _key()
    body = {"kind": "system.noop", "params": {"sleep_ms": 0}}
    a = client.post("/api/v1/system/jobs", json=body, headers={"Idempotency-Key": key})
    b = client.post("/api/v1/system/jobs", json=body, headers={"Idempotency-Key": key})
    assert a.json()["id"] == b.json()["id"]
    assert a.headers["Idempotent-Replayed"] == "false" and b.headers["Idempotent-Replayed"] == "true"
    c = client.post(
        "/api/v1/system/jobs",
        json={"kind": "system.noop", "params": {"sleep_ms": 1}},
        headers={"Idempotency-Key": key},
    )
    assert c.status_code == 409


def test_missing_idempotency_key_is_422(client):
    assert client.post("/api/v1/system/jobs", json={"kind": "system.noop"}).status_code == 422


def test_failed_job_records_error(client, runner):
    r = client.post(
        "/api/v1/system/jobs",
        json={"params": {"sleep_ms": 0, "fail": True}},
        headers={"Idempotency-Key": _key()},
    )
    job_id = r.json()["id"]
    assert wait_until(lambda: jobs.get_job(job_id).status == "failed")
    assert jobs.get_job(job_id).last_error["class"] == "RuntimeError"


def test_cancel_queued_job(client):
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 0}, _key())
    r = client.post(f"/api/v1/system/jobs/{job.id}/cancel")
    assert r.json()["status"] == "cancelled"
    assert executor.claim_next("w") is None


def test_cancel_running_job(client, runner):
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 3000}, _key())
    assert wait_until(lambda: jobs.get_job(job.id).status == "running")
    client.post(f"/api/v1/system/jobs/{job.id}/cancel")
    assert wait_until(lambda: jobs.get_job(job.id).status == "cancelled", timeout=5)


def test_fencing_rejects_stale_worker(data_dir):
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 0}, _key())
    claim = executor.claim_next("old-worker")
    assert claim is not None
    with tx() as s:  # 模拟租约被收回并由新执行接管
        s.execute(text("UPDATE jobs SET fencing_token = fencing_token + 1 WHERE id = :id"), {"id": job.id})
    assert executor.execute_claimed(claim, "old-worker") == "lease_lost"
    assert jobs.get_job(job.id).status == "running"


def _force_running(job_id: str, attempt: int = 1) -> None:
    with tx() as s:
        s.execute(
            text(
                """UPDATE jobs SET status='running', attempt=:a, lease_owner='dead', lease_expires_at=:le,
                          fencing_token=fencing_token+1 WHERE id=:id"""
            ),
            {"a": attempt, "le": "2099-01-01T00:00:00.000Z", "id": job_id},
        )


def test_restart_requeues_interrupted_job_and_finishes(data_dir):
    """模拟应用在任务执行中被强制关闭：重启后任务重新排队并完成。"""
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 0}, _key())
    _force_running(job.id)
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()  # start() 先执行启动恢复
    try:
        assert wait_until(lambda: jobs.get_job(job.id).status == "succeeded")
    finally:
        r.stop()
    final = jobs.get_job(job.id)
    assert final.attempt == 2 and final.result["attempt"] == 2


def test_restart_fails_job_after_max_attempts(data_dir):
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 0}, _key())
    _force_running(job.id, attempt=3)
    assert executor.recover_interrupted(on_startup=True)["failed"] == 1
    j = jobs.get_job(job.id)
    assert j.status == "failed" and j.last_error["class"] == "Interrupted"


def test_external_action_is_never_auto_retried(data_dir):
    register(
        JobKind(
            name="test.external_action",
            queue="external_actions",
            handler=lambda rt, p: {},
            user_creatable=True,
            retry_on_interrupt=False,
        )
    )
    job, _ = jobs.create_job("test.external_action", {}, _key())
    _force_running(job.id)
    assert executor.recover_interrupted(on_startup=True) == {"requeued": 0, "failed": 1, "cancelled": 0}


def test_periodic_reaper_only_touches_expired_leases(data_dir):
    job, _ = jobs.create_job("system.noop", {"sleep_ms": 0}, _key())
    _force_running(job.id)  # 租约到 2099 年
    assert executor.recover_interrupted(on_startup=False) == {"requeued": 0, "failed": 0, "cancelled": 0}
    with tx() as s:
        s.execute(
            text("UPDATE jobs SET lease_expires_at=:t WHERE id=:id"),
            {"t": "2000-01-01T00:00:00.000Z", "id": job.id},
        )
    assert executor.recover_interrupted(on_startup=False)["requeued"] == 1
    assert json.loads(json.dumps(jobs.get_job(job.id).model_dump()))["status"] == "queued"
    assert utcnow_iso() > "2026"
