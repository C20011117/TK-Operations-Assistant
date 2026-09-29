"""系统接口：健康检查、M0 测试任务、外部连通检查。"""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from tk_workspace.api.deps import TenantCtx, require_roles
from tk_workspace.config import get_settings
from tk_workspace.integrations.fastmoss import operations as fm_ops
from tk_workspace.integrations.fastmoss.mcp_client import FastMossError
from tk_workspace.platform.db.context import ExecutionContext
from tk_workspace.platform.db.session import runtime_tx
from tk_workspace.platform.jobs import service as jobs
from tk_workspace.platform.llm import gateway

health_router = APIRouter(prefix="/health", tags=["system"])
tenant_router = APIRouter(prefix="/tenants/{tenant_id}/system", tags=["system"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str]


@health_router.get("", response_model=Health, summary="进程存活")
async def live() -> Health:
    return Health(status="ok", checks={"api": "ok"})


@health_router.get("/ready", response_model=Health, summary="依赖就绪（数据库、Redis）")
async def ready(response: Response) -> Health:
    checks: dict[str, str] = {}
    try:
        async with runtime_tx() as s:
            await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        r = aioredis.from_url(get_settings().redis_url, socket_timeout=2)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Health(status="ok" if ok else "degraded", checks=checks)


class CreateJobRequest(BaseModel):
    kind: Literal["system.noop"] = "system.noop"
    params: dict[str, Any] = Field(default_factory=dict)


IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]


@tenant_router.post("/jobs", response_model=jobs.JobView, status_code=202, summary="创建测试任务（幂等）")
async def create_job(body: CreateJobRequest, ctx: TenantCtx, idempotency_key: IdemKey, response: Response):
    if ctx.role == "viewer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail={"code": "forbidden", "message": "只读成员不能创建任务"}
        )
    try:
        job, created = await jobs.create_job(ctx, body.kind, body.params, idempotency_key)
    except jobs.IdempotencyConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_conflict", "message": "同一个 Idempotency-Key 已用于不同内容的请求"},
        ) from None
    response.headers["Idempotent-Replayed"] = "false" if created else "true"
    return job


@tenant_router.get("/jobs", response_model=list[jobs.JobView], summary="我的任务")
async def list_jobs(ctx: TenantCtx) -> list[jobs.JobView]:
    return await jobs.list_jobs(ctx)


@tenant_router.get("/jobs/{job_id}", response_model=jobs.JobView, summary="任务状态")
async def get_job(job_id: UUID, ctx: TenantCtx) -> jobs.JobView:
    job = await jobs.get_job(ctx, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": "资源不存在"})
    return job


class ExternalCheck(BaseModel):
    status: Literal["ok", "not_configured", "error"]
    detail: str = ""
    model: str | None = None
    latency_ms: int | None = None
    available_credits: int | None = None
    credit_cost: int | None = None


class ExternalChecks(BaseModel):
    llm: ExternalCheck
    fastmoss: ExternalCheck


def _fastmoss_check() -> ExternalCheck:
    if not get_settings().fastmoss_mcp_api_key.get_secret_value():
        return ExternalCheck(status="not_configured", detail="未配置 FASTMOSS_MCP_API_KEY")
    try:
        res = fm_ops.execute("account.credits")
    except FastMossError as exc:
        return ExternalCheck(status="error", detail=f"{exc.code}: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001
        return ExternalCheck(status="error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
    balance = (res.data or {}).get("balance", {}) if isinstance(res.data, dict) else {}
    return ExternalCheck(
        status="ok",
        detail="credit_usage_summary",
        available_credits=balance.get("available_credits"),
        credit_cost=res.charge.credit_cost if res.charge else None,
    )


@tenant_router.post("/checks", response_model=ExternalChecks, summary="外部服务连通检查（仅管理员）")
async def external_checks(
    ctx: Annotated[ExecutionContext, Depends(require_roles("admin"))],
) -> ExternalChecks:
    llm_res, fm_res = await asyncio.gather(
        asyncio.to_thread(gateway.check), asyncio.to_thread(_fastmoss_check)
    )
    return ExternalChecks(
        llm=ExternalCheck(
            status=llm_res.status, detail=llm_res.detail, model=llm_res.model, latency_ms=llm_res.latency_ms
        ),
        fastmoss=fm_res,
    )
