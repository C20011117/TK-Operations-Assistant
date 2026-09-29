"""任务创建、查询与取消。任务记录与幂等记录在同一事务提交。"""

import hashlib
import json
import threading
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs.registry import get_kind

JobStatus = Literal[
    "queued", "running", "waiting_input", "cancel_requested", "succeeded", "partial", "failed", "cancelled"
]
TERMINAL = ("succeeded", "partial", "failed", "cancelled")

# 新任务入队时唤醒执行器，避免等待下一次轮询
job_enqueued = threading.Event()


class JobView(BaseModel):
    id: str
    kind: str
    status: JobStatus
    stage: str | None
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    last_error: dict[str, Any] | None
    attempt: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class IdempotencyConflict(Exception):
    """同一个 Idempotency-Key 用在了内容不同的请求上。"""


class UnknownJobKind(Exception):
    pass


class JobNotFound(Exception):
    pass


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_JOB_COLUMNS = "id, kind, status, stage, progress, result, last_error, attempt, created_at, updated_at, started_at, finished_at"
_SQL_JOB_BY_ID = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = :id"  # noqa: S608 — 列名为模块常量
_SQL_JOB_LIST = f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT :n"  # noqa: S608


def _view(row: Any) -> JobView:
    data = dict(row)
    for key in ("progress", "result", "last_error"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    return JobView(**data)


def _get(s: Session, job_id: str) -> JobView | None:
    row = s.execute(text(_SQL_JOB_BY_ID), {"id": job_id}).mappings().first()
    return _view(row) if row else None


def create_job(kind: str, params: dict[str, Any], idempotency_key: str) -> tuple[JobView, bool]:
    """创建任务。返回 (任务, 是否新建)。相同 key + 相同内容返回原任务；内容不同抛出冲突。"""
    job_kind = get_kind(kind)
    if not job_kind or not job_kind.user_creatable:
        raise UnknownJobKind(kind)
    if len(json.dumps(params)) > job_kind.max_params_bytes:
        raise ValueError("params too large")
    action = f"jobs.create:{kind}"
    rhash = request_hash({"kind": kind, "params": params})
    now = utcnow_iso()
    with tx() as s:
        existing = s.execute(
            text("SELECT request_hash, resource_id FROM idempotency_records WHERE action = :a AND key = :k"),
            {"a": action, "k": idempotency_key},
        ).first()
        if existing:
            if existing.request_hash != rhash:
                raise IdempotencyConflict(idempotency_key)
            job = _get(s, existing.resource_id)
            assert job is not None
            return job, False
        job_id = str(uuid4())
        s.execute(
            text(
                """INSERT INTO jobs (id, kind, queue, status, params, max_attempts, created_at, updated_at)
                   VALUES (:id, :k, :q, 'queued', :p, :m, :now, :now)"""
            ),
            {
                "id": job_id,
                "k": kind,
                "q": job_kind.queue,
                "p": json.dumps(params, ensure_ascii=False),
                "m": job_kind.max_attempts,
                "now": now,
            },
        )
        s.execute(
            text(
                """INSERT INTO idempotency_records (id, action, key, request_hash, resource_id, created_at)
                   VALUES (:id, :a, :k, :h, :r, :now)"""
            ),
            {"id": str(uuid4()), "a": action, "k": idempotency_key, "h": rhash, "r": job_id, "now": now},
        )
        job = _get(s, job_id)
    job_enqueued.set()
    assert job is not None
    return job, True


def enqueue_in_tx(s: Session, kind: str, params: dict[str, Any]) -> str:
    """在调用方的事务里创建内部任务（业务记录与任务同一事务提交）。提交后调用 job_enqueued.set() 唤醒执行器。"""
    job_kind = get_kind(kind)
    if job_kind is None:
        raise UnknownJobKind(kind)
    job_id = str(uuid4())
    now = utcnow_iso()
    s.execute(
        text(
            """INSERT INTO jobs (id, kind, queue, status, params, max_attempts, created_at, updated_at)
               VALUES (:id, :k, :q, 'queued', :p, :m, :now, :now)"""
        ),
        {
            "id": job_id,
            "k": kind,
            "q": job_kind.queue,
            "p": json.dumps(params, ensure_ascii=False),
            "m": job_kind.max_attempts,
            "now": now,
        },
    )
    return job_id


def get_job(job_id: str) -> JobView | None:
    with tx() as s:
        return _get(s, job_id)


def list_jobs(limit: int = 20) -> list[JobView]:
    with tx() as s:
        rows = s.execute(text(_SQL_JOB_LIST), {"n": min(limit, 100)}).mappings().all()
    return [_view(r) for r in rows]


def cancel_job(job_id: str) -> JobView:
    """排队中的任务直接取消；执行中的任务标记为“取消中”，由 handler 在下一次报告进度时停止。"""
    now = utcnow_iso()
    with tx() as s:
        job = _get(s, job_id)
        if job is None:
            raise JobNotFound(job_id)
        if job.status == "queued":
            s.execute(
                text(
                    """UPDATE jobs SET status='cancelled', cancel_requested=1, finished_at=:now, updated_at=:now
                       WHERE id=:id AND status='queued'"""
                ),
                {"id": job_id, "now": now},
            )
        elif job.status == "running":
            s.execute(
                text(
                    """UPDATE jobs SET status='cancel_requested', cancel_requested=1, updated_at=:now
                       WHERE id=:id AND status='running'"""
                ),
                {"id": job_id, "now": now},
            )
        job = _get(s, job_id)
    assert job is not None
    return job
