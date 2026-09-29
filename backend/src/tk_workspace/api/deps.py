"""请求依赖：会话认证、CSRF 校验、企业上下文。路径中的 tenant_id 只用于选择，不作为授权依据。"""

from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from tk_workspace.modules.identity import security, service
from tk_workspace.platform.db.context import ExecutionContext, Role

SESSION_COOKIE = "tkws_session"
CSRF_COOKIE = "tkws_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def current_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> service.ResolvedSession:
    if not session_token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthenticated", "message": "请先登录"}
        )
    try:
        resolved = await service.resolve_session(session_token)
    except service.AuthError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "session_invalid", "message": "登录已失效，请重新登录"},
        ) from None
    if request.method not in _SAFE_METHODS:
        if not csrf_header or not security.digest_matches(csrf_header, resolved.csrf_hash):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed", "message": "请求校验失败"}
            )
    return resolved


SessionDep = Annotated[service.ResolvedSession, Depends(current_session)]


def tenant_context(tenant_id: UUID, session: SessionDep) -> ExecutionContext:
    ctx = session.context
    if ctx is None or ctx.tenant_id != tenant_id:
        # 不暴露该企业是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": "资源不存在"})
    return ctx


TenantCtx = Annotated[ExecutionContext, Depends(tenant_context)]


def require_roles(*roles: Role):
    def checker(ctx: TenantCtx) -> ExecutionContext:
        if ctx.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail={"code": "forbidden", "message": "当前角色无此权限"}
            )
        return ctx

    return checker
