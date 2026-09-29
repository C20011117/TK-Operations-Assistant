"""FastAPI 入口。路由统一挂在 /api/v1 下；错误响应统一为 {"error": {"code", "message"}}。"""

import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tk_workspace.api import system
from tk_workspace.api.deps import current_session
from tk_workspace.config import get_settings
from tk_workspace.modules.campaigns import markets
from tk_workspace.modules.identity import router as identity_router

log = logging.getLogger("tk_workspace")


def create_app() -> FastAPI:
    settings = get_settings()
    missing = settings.missing_required()
    if missing:
        # 只报告缺失的变量名，不输出任何值
        log.warning("missing required settings: %s", ", ".join(missing))

    app = FastAPI(
        title="TK 多站点达人工作台 API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs" if settings.is_dev_like else None,
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
    app.include_router(system.health_router, prefix=api)
    app.include_router(identity_router.router, prefix=api)
    app.include_router(markets.router, prefix=api, dependencies=[Depends(current_session)])
    app.include_router(system.tenant_router, prefix=api)
    return app


app = create_app()
