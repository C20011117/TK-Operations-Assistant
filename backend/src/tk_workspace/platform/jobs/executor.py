"""执行器：领取租约 → 执行 handler → 以 fencing token 写回结果；以及中断任务的恢复。

- 领取是一条原子 UPDATE … RETURNING，同一任务不会被两个线程同时领取。
- 每次报告进度都会续租；租约被收回（或用户取消）后，旧执行的写入因 fencing token 不匹配而被拒绝。
- 应用被关闭或崩溃后重启：执行中的任务按任务类型规则重新排队或标记失败，并写明原因。
"""

import json
import logging
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from tk_workspace.config import get_settings
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs.registry import JobRuntime, get_kind

log = logging.getLogger(__name__)


def _lease_until(seconds: int | None = None) -> str:
    secs = seconds or get_settings().job_lease_seconds
    t = datetime.now(UTC) + timedelta(seconds=secs)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def claim_next(worker_id: str) -> dict[str, Any] | None:
    now = utcnow_iso()
    with tx() as s:
        row = s.execute(
            text(
                """UPDATE jobs
                   SET status='running', lease_owner=:w, lease_expires_at=:le,
                       fencing_token=fencing_token + 1, attempt=attempt + 1,
                       started_at=COALESCE(started_at, :now), updated_at=:now
                   WHERE id = (SELECT id FROM jobs WHERE status='queued' AND cancel_requested=0
                               ORDER BY created_at LIMIT 1)
                     AND status='queued'
                   RETURNING id, kind, params, fencing_token, attempt"""
            ),
            {"w": worker_id, "le": _lease_until(), "now": now},
        ).first()
        if row is None:
            return None
        attempt_id = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO job_attempts (id, job_id, attempt_no, fencing_token, worker_id, started_at)
                   VALUES (:id, :j, :n, :tok, :w, :now)"""
            ),
            {
                "id": attempt_id,
                "j": row.id,
                "n": row.attempt,
                "tok": row.fencing_token,
                "w": worker_id,
                "now": now,
            },
        )
    return {
        "id": row.id,
        "kind": row.kind,
        "params": json.loads(row.params or "{}"),
        "token": row.fencing_token,
        "attempt": row.attempt,
        "attempt_id": attempt_id,
    }


def execute_claimed(claim: dict[str, Any], worker_id: str) -> str:
    """执行已领取的任务，返回 succeeded / failed / cancelled / lease_lost。"""
    job_id, token = claim["id"], claim["token"]

    def report_progress(progress: dict[str, Any]) -> bool:
        with tx() as s:
            res = s.execute(
                text(
                    """UPDATE jobs SET progress=:p, lease_expires_at=:le, updated_at=:now
                       WHERE id=:id AND fencing_token=:tok AND status='running'"""
                ),
                {
                    "p": json.dumps(progress, ensure_ascii=False),
                    "le": _lease_until(),
                    "now": utcnow_iso(),
                    "id": job_id,
                    "tok": token,
                },
            )
            return res.rowcount == 1

    job_kind = get_kind(claim["kind"])
    outcome, result, error = "succeeded", None, None
    try:
        if job_kind is None:
            raise LookupError(f"unregistered job kind: {claim['kind']}")
        result = job_kind.handler(
            JobRuntime(job_id, worker_id, claim["attempt"], report_progress), claim["params"]
        )
    except Exception as exc:  # noqa: BLE001  任务失败要记录为可理解的错误，而不是让执行线程退出
        outcome = "failed"
        error = {"class": type(exc).__name__, "message": str(exc)[:500]}
        log.error("job %s failed: %s", job_id, traceback.format_exc(limit=5))

    now = utcnow_iso()
    with tx() as s:
        current = s.execute(
            text("SELECT status, fencing_token FROM jobs WHERE id=:id"), {"id": job_id}
        ).first()
        if (
            current is None
            or current.fencing_token != token
            or current.status not in ("running", "cancel_requested")
        ):
            final = "lease_lost"
        else:
            final = "cancelled" if current.status == "cancel_requested" else outcome
            s.execute(
                text(
                    """UPDATE jobs SET status=:st, result=:r, last_error=:e,
                              progress=CASE WHEN :st='succeeded' THEN '{"percent": 100}' ELSE progress END,
                              lease_owner=NULL, lease_expires_at=NULL, finished_at=:now, updated_at=:now
                       WHERE id=:id AND fencing_token=:tok"""
                ),
                {
                    "st": final,
                    "r": json.dumps(result, ensure_ascii=False) if result is not None else None,
                    "e": json.dumps(error, ensure_ascii=False) if error else None,
                    "now": now,
                    "id": job_id,
                    "tok": token,
                },
            )
        s.execute(
            text("UPDATE job_attempts SET finished_at=:now, outcome=:o, error_class=:ec WHERE id=:id"),
            {"now": now, "o": final, "ec": error["class"] if error else None, "id": claim["attempt_id"]},
        )
    return final


def recover_interrupted(on_startup: bool) -> dict[str, int]:
    """恢复中断的任务。

    on_startup=True：本进程刚启动，之前所有“执行中”的任务都已没有执行者，全部处理；
    否则只处理租约已过期的任务。返回 {"requeued": n, "failed": n, "cancelled": n}。
    """
    now = utcnow_iso()
    counts = {"requeued": 0, "failed": 0, "cancelled": 0}
    with tx() as s:
        cond = "status IN ('running','cancel_requested')"
        if not on_startup:
            cond += " AND lease_expires_at < :now"
        rows = s.execute(
            text(f"SELECT id, kind, status, attempt, max_attempts FROM jobs WHERE {cond}"),  # noqa: S608
            {"now": now},
        ).all()
        for r in rows:
            kind = get_kind(r.kind)
            interrupted = {"class": "Interrupted", "message": "应用在任务执行过程中被关闭或中断"}
            if r.status == "cancel_requested":
                new_status, key = "cancelled", "cancelled"
            elif kind and kind.retry_on_interrupt and r.attempt < r.max_attempts:
                new_status, key = "queued", "requeued"
            else:
                new_status, key = "failed", "failed"
            s.execute(
                text(
                    """UPDATE jobs SET status=:st, last_error=:e, lease_owner=NULL, lease_expires_at=NULL,
                              finished_at=CASE WHEN :st='queued' THEN NULL ELSE :now END, updated_at=:now
                       WHERE id=:id"""
                ),
                {"st": new_status, "e": json.dumps(interrupted, ensure_ascii=False), "now": now, "id": r.id},
            )
            s.execute(
                text(
                    """UPDATE job_attempts SET finished_at=:now, outcome='interrupted'
                       WHERE job_id=:id AND finished_at IS NULL"""
                ),
                {"now": now, "id": r.id},
            )
            counts[key] += 1
    if any(counts.values()):
        log.warning("recovered interrupted jobs: %s", counts)
    return counts
