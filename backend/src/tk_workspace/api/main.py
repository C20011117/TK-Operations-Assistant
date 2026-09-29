"""FastAPI 应用工厂。路由统一挂在 /api/v1 下；错误响应统一为 {"error": {"code", "message"}}。"""

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tk_workspace.api import system
from tk_workspace.api.security import LocalAuthMiddleware
from tk_workspace.config import APP_VERSION, Settings, get_settings
from tk_workspace.modules.campaigns import markets
from tk_workspace.modules.settings import router as settings_router

log = logging.getLogger("tk_workspace")
DEV_TOKEN = "dev-token"  # noqa: S105 — 仅开发模式（未由外壳提供令牌时）使用，生产启动会拒绝


def resolve_token(settings: Settings) -> str:
    if settings.launch_token:
        return settings.launch_token
    if settings.app_env == "production":
        raise RuntimeError("TKWS_LAUNCH_TOKEN is required in production")
    return DEV_TOKEN


def create_app(settings: Settings | None = None, token: str | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(
        title="TK 达人工作台（桌面版）本机接口",
        version=APP_VERSION,
        openapi_url="/api/v1/openapi.json" if s.app_env != "production" else None,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(
            {"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            {"error": {"code": "validation_error", "message": "请求参数不正确", "fields": fields}},
            status_code=422,
        )

    api = "/api/v1"
    app.include_router(system.router, prefix=api)
    app.include_router(markets.router, prefix=api)
    app.include_router(settings_router.router, prefix=api)

    # 中间件后加的在外层：CORS 在最外层处理预检，其次是令牌校验
    app.add_middleware(LocalAuthMiddleware, token=token or resolve_token(s))
    origins = list(s.allowed_origins)
    if s.app_env != "production":
        origins += ["http://localhost:5173", "http://127.0.0.1:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["Idempotent-Replayed", "X-Request-ID"],
    )
    return app
