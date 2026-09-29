from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status

from tk_workspace.api.deps import CSRF_COOKIE, SESSION_COOKIE, SessionDep
from tk_workspace.config import get_settings
from tk_workspace.modules.identity import service
from tk_workspace.modules.identity.schemas import LoginRequest, Me, SwitchTenantRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookies(response: Response, issued: service.IssuedSession) -> None:
    secure = get_settings().app_env in ("staging", "production")
    max_age = int(get_settings().session_ttl_hours * 3600)
    response.set_cookie(
        SESSION_COOKIE, issued.token, max_age=max_age, httponly=True, secure=secure, samesite="lax", path="/"
    )
    # CSRF 令牌给前端读取后放进请求头（双重提交）
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=Me, summary="邮箱密码登录")
async def login(body: LoginRequest, response: Response) -> Me:
    try:
        issued, _ = await service.login(body.email, body.password)
    except service.AuthError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_credentials", "message": "邮箱或密码不正确"},
        ) from None
    _set_cookies(response, issued)
    resolved = await service.resolve_session(issued.token)
    return await service.get_me(resolved)


@router.post("/logout", status_code=204, summary="退出登录")
async def logout(
    session: SessionDep,
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Response:
    if session_token:
        await service.logout(session_token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=Me, summary="当前用户与企业")
async def me(session: SessionDep) -> Me:
    return await service.get_me(session)


@router.post("/switch-tenant", response_model=Me, summary="切换当前企业")
async def switch_tenant(
    body: SwitchTenantRequest,
    session: SessionDep,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Me:
    try:
        await service.switch_tenant(session, session_token or "", body.tenant_id)
    except service.AuthError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": "资源不存在"}
        ) from None
    return await service.get_me(await service.resolve_session(session_token or ""))
