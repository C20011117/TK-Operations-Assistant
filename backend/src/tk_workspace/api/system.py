"""系统接口：健康检查、版本信息、测试任务。"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from tk_workspace.config import APP_VERSION, get_settings
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.jobs import service as jobs

router = APIRouter(prefix="/system", tags=["system"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, str]


@router.get("/health", response_model=Health, summary="健康检查（数据库）")
def health(response: Response) -> Health:
    checks: dict[str, str] = {}
    try:
        with tx() as s:
            s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Health(status="ok" if ok else "degraded", version=APP_VERSION, checks=checks)


class AppInfo(BaseModel):
    version: str
    data_dir: str


@router.get("/info", response_model=AppInfo, summary="版本与数据目录")
def info() -> AppInfo:
    return AppInfo(version=APP_VERSION, data_dir=str(get_settings().data_dir))


class CreateJobRequest(BaseModel):
    kind: Literal["system.noop"] = "system.noop"
    params: dict[str, Any] = Field(default_factory=dict)


IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
_NOT_FOUND = {"code": "not_found", "message": "任务不存在"}


@router.post("/jobs", response_model=jobs.JobView, status_code=202, summary="创建测试任务（幂等）")
def create_job(body: CreateJobRequest, idempotency_key: IdemKey, response: Response) -> jobs.JobView:
    try:
        job, created = jobs.create_job(body.kind, body.params, idempotency_key)
    except jobs.IdempotencyConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_conflict", "message": "同一个 Idempotency-Key 已用于不同内容的请求"},
        ) from None
    response.headers["Idempotent-Replayed"] = "false" if created else "true"
    return job


@router.get("/jobs", response_model=list[jobs.JobView], summary="最近的任务")
def list_jobs() -> list[jobs.JobView]:
    return jobs.list_jobs()


@router.get("/jobs/{job_id}", response_model=jobs.JobView, summary="任务状态")
def get_job(job_id: str) -> jobs.JobView:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=jobs.JobView, summary="取消任务")
def cancel_job(job_id: str) -> jobs.JobView:
    try:
        return jobs.cancel_job(job_id)
    except jobs.JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from None
