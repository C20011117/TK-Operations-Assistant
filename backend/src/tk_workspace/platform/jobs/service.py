"""任务创建与查询。业务状态、任务记录与 outbox 事件在同一事务提交（ADR-03）。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tk_workspace.platform.db.context import ExecutionContext
from tk_workspace.platform.db.session import runtime_tx
from tk_workspace.platform.jobs.registry import get_kind

JobStatus = Literal[
    "queued", "running", "waiting_input", "cancel_requested", "succeeded", "partial", "failed", "cancelled"
]
IDEMPOTENCY_TTL = timedelta(hours=24)


class JobView(BaseModel):
    id: UUID
    kind: str
    status: JobStatus
    stage: str | None
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    last_error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class IdempotencyConflict(Exception):
    """同一个 Idempotency-Key 用在了内容不同的请求上。"""


class UnknownJobKind(Exception):
    pass


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_JOB_COLUMNS = "id, kind, status, stage, progress, result, last_error, created_at, updated_at, finished_at"
# 列名来自上面的模块常量，不含任何外部输入
_SQL_JOB_BY_ID = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = :id"  # noqa: S608
_SQL_JOB_LIST = f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT :n"  # noqa: S608


def _view(row: Any) -> JobView:
    data = dict(row)
    # asyncpg 对未声明类型的 jsonb 返回字符串，这里统一解析
    for key in ("progress", "result", "last_error"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    return JobView(**data)


async def _get_job(s: AsyncSession, job_id: UUID) -> JobView | None:
    row = (await s.execute(text(_SQL_JOB_BY_ID), {"id": job_id})).mappings().first()
    return _view(row) if row else None


async def create_job(
    ctx: ExecutionContext, kind: str, params: dict[str, Any], idempotency_key: str
) -> tuple[JobView, bool]:
    """创建任务。返回 (任务, 是否新建)。相同 key + 相同内容返回原任务；内容不同抛出冲突。"""
    job_kind = get_kind(kind)
    if not job_kind or not job_kind.user_creatable:
        raise UnknownJobKind(kind)
    if len(json.dumps(params)) > job_kind.max_params_bytes:
        raise ValueError("params too large")
    action = f"jobs.create:{kind}"
    rhash = request_hash({"kind": kind, "params": params})
    async with runtime_tx(ctx.as_settings()) as s:
        inserted = (
            await s.execute(
                text(
                    """INSERT INTO idempotency_records
                         (id, tenant_id, principal_id, action, key, request_hash, status, expires_at)
                       VALUES (:id, :t, :p, :a, :k, :h, 'in_progress', :e)
                       ON CONFLICT (tenant_id, principal_id, action, key) DO NOTHING
                       RETURNING id"""
                ),
                {
                    "id": uuid4(),
                    "t": ctx.tenant_id,
                    "p": ctx.principal_id,
                    "a": action,
                    "k": idempotency_key,
                    "h": rhash,
                    "e": datetime.now(UTC) + IDEMPOTENCY_TTL,
                },
            )
        ).first()
        if inserted is None:
            existing = (
                await s.execute(
                    text(
                        """SELECT request_hash, resource_id FROM idempotency_records
                           WHERE tenant_id = :t AND principal_id = :p AND action = :a AND key = :k FOR UPDATE"""
                    ),
                    {"t": ctx.tenant_id, "p": ctx.principal_id, "a": action, "k": idempotency_key},
                )
            ).one()
            if existing.request_hash != rhash:
                raise IdempotencyConflict(idempotency_key)
            job = await _get_job(s, existing.resource_id)
            assert job is not None
            return job, False

        job_id = uuid4()
        await s.execute(
            text(
                """INSERT INTO jobs (id, tenant_id, kind, owner_principal_id, requested_by, params)
                   VALUES (:id, :t, :k, :p, :p, CAST(:params AS jsonb))"""
            ),
            {
                "id": job_id,
                "t": ctx.tenant_id,
                "k": kind,
                "p": ctx.principal_id,
                "params": json.dumps(params),
            },
        )
        await s.execute(
            text(
                """INSERT INTO outbox_events (id, tenant_id, aggregate_type, aggregate_id, aggregate_version,
                                              event_type, payload)
                   VALUES (:id, :t, 'job', :j, 1, 'job.enqueued', CAST(:payload AS jsonb))"""
            ),
            {
                "id": uuid4(),
                "t": ctx.tenant_id,
                "j": job_id,
                "payload": json.dumps({"job_id": str(job_id), "queue": job_kind.queue}),
            },
        )
        await s.execute(
            text(
                """UPDATE idempotency_records SET status = 'completed', resource_id = :j
                   WHERE id = :id"""
            ),
            {"j": job_id, "id": inserted.id},
        )
        job = await _get_job(s, job_id)
        assert job is not None
        return job, True


async def get_job(ctx: ExecutionContext, job_id: UUID) -> JobView | None:
    async with runtime_tx(ctx.as_settings()) as s:
        return await _get_job(s, job_id)


async def list_jobs(ctx: ExecutionContext, limit: int = 20) -> list[JobView]:
    async with runtime_tx(ctx.as_settings()) as s:
        rows = (
            (
                await s.execute(
                    text(_SQL_JOB_LIST),
                    {"n": min(limit, 100)},
                )
            )
            .mappings()
            .all()
        )
    return [_view(r) for r in rows]
