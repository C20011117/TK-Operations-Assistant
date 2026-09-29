"""Worker 端执行器：领取租约 → 执行 handler → 以 fencing token 写回结果。

- 队列消息只含 job_id；企业与执行身份从数据库任务记录解析，不信任消息内容。
- 至少一次投递：重复消息遇到非可执行状态直接跳过。
- 租约过期被他人接管后，旧 worker 的 fencing token 不再匹配，写入会被拒绝。
"""

import json
import logging
import socket
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from tk_workspace.config import get_settings
from tk_workspace.platform.db.context import ExecutionContext
from tk_workspace.platform.db.session import runtime_tx_sync
from tk_workspace.platform.jobs.registry import JobRuntime, get_kind

log = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid4().hex[:8]}"


def _ctx_settings(job_id: str, tenant_id: UUID, principal_id: UUID) -> dict[str, str]:
    return {
        "app.claim_job_id": job_id,
        "app.tenant_id": str(tenant_id),
        "app.principal_id": str(principal_id),
    }


def execute_job(job_id: str, worker_id: str | None = None) -> str:
    """执行一个任务，返回结果标签：succeeded / failed / skipped / lease_lost / cancelled。"""
    worker_id = worker_id or default_worker_id()
    lease = timedelta(seconds=get_settings().job_lease_seconds)

    # 1) 领取：先凭 claim_job_id 读到任务，再切换到该任务所属企业的上下文
    with runtime_tx_sync({"app.claim_job_id": job_id}) as s:
        row = s.execute(
            text(
                """SELECT id, tenant_id, kind, status, params, requested_by, owner_principal_id,
                          lease_expires_at, fencing_token, cancel_requested_at
                   FROM jobs WHERE id = :id FOR UPDATE"""
            ),
            {"id": job_id},
        ).first()
        if row is None:
            log.warning("job %s not found", job_id)
            return "skipped"
        now = datetime.now(UTC)
        runnable = row.status == "queued" or (
            row.status == "running" and row.lease_expires_at is not None and row.lease_expires_at < now
        )
        principal_id = row.owner_principal_id or row.requested_by
        settings = _ctx_settings(job_id, row.tenant_id, principal_id)
        for k, v in settings.items():
            s.execute(text("SELECT set_config(:k, :v, true)"), {"k": k, "v": v})
        if row.cancel_requested_at is not None and row.status in ("queued", "cancel_requested"):
            s.execute(
                text("UPDATE jobs SET status='cancelled', finished_at=now(), updated_at=now() WHERE id=:id"),
                {"id": job_id},
            )
            return "cancelled"
        if not runnable:
            return "skipped"
        token = row.fencing_token + 1
        attempt_no = s.execute(
            text("SELECT count(*) + 1 FROM job_attempts WHERE job_id = :id"), {"id": job_id}
        ).scalar_one()
        s.execute(
            text(
                """UPDATE jobs SET status='running', lease_owner=:w, lease_expires_at=:le, fencing_token=:tok,
                          updated_at=now(), lock_version = lock_version + 1
                   WHERE id=:id"""
            ),
            {"w": worker_id, "le": now + lease, "tok": token, "id": job_id},
        )
        attempt_id = uuid4()
        s.execute(
            text(
                """INSERT INTO job_attempts (id, tenant_id, job_id, attempt_no, fencing_token, worker_id)
                   VALUES (:id, :t, :j, :n, :tok, :w)"""
            ),
            {
                "id": attempt_id,
                "t": row.tenant_id,
                "j": job_id,
                "n": attempt_no,
                "tok": token,
                "w": worker_id,
            },
        )
        kind, params, tenant_id = row.kind, dict(row.params or {}), row.tenant_id

    ctx = ExecutionContext(tenant_id=tenant_id, user_id=None, principal_id=principal_id, role=None)

    def report_progress(progress: dict[str, Any]) -> bool:
        with runtime_tx_sync(settings) as s2:
            res = s2.execute(
                text(
                    """UPDATE jobs SET progress = CAST(:p AS jsonb), updated_at = now()
                       WHERE id = :id AND fencing_token = :tok AND status = 'running'"""
                ),
                {"p": json.dumps(progress, ensure_ascii=False), "id": job_id, "tok": token},
            )
            return res.rowcount == 1

    # 2) 执行
    job_kind = get_kind(kind)
    outcome, result, error = "succeeded", None, None
    try:
        if job_kind is None:
            raise LookupError(f"unregistered job kind: {kind}")
        result = job_kind.handler(JobRuntime(job_id, worker_id, ctx, report_progress), params)
    except Exception as exc:  # noqa: BLE001  任务失败要记录为可理解的错误，而不是让 worker 崩溃
        outcome = "failed"
        error = {"class": type(exc).__name__, "message": str(exc)[:500]}
        log.error("job %s failed: %s", job_id, traceback.format_exc(limit=5))

    # 3) 写回：fencing token 不匹配说明租约已被接管，本次结果作废
    with runtime_tx_sync(settings) as s:
        res = s.execute(
            text(
                """UPDATE jobs SET status = :st, result = CAST(:r AS jsonb), last_error = CAST(:e AS jsonb),
                          progress = CASE WHEN :st = 'succeeded' THEN '{"percent": 100}'::jsonb ELSE progress END,
                          lease_owner = NULL, lease_expires_at = NULL, finished_at = now(), updated_at = now(),
                          lock_version = lock_version + 1
                   WHERE id = :id AND fencing_token = :tok AND status = 'running'"""
            ),
            {
                "st": outcome,
                "r": json.dumps(result, ensure_ascii=False) if result is not None else None,
                "e": json.dumps(error, ensure_ascii=False) if error else None,
                "id": job_id,
                "tok": token,
            },
        )
        final = outcome if res.rowcount == 1 else "lease_lost"
        s.execute(
            text(
                """UPDATE job_attempts SET finished_at = now(), outcome = :o, error_class = :ec
                   WHERE id = :id"""
            ),
            {"o": final, "ec": error["class"] if error else None, "id": attempt_id},
        )
    return final
