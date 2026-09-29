"""身份服务：登录、会话解析、企业选择。其他模块只通过这里获得 ExecutionContext，不自己解析 Cookie。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from tk_workspace.config import get_settings
from tk_workspace.modules.identity import security
from tk_workspace.modules.identity.schemas import Me, TenantMembership
from tk_workspace.platform.db.context import ExecutionContext
from tk_workspace.platform.db.session import apply_context_async, runtime_tx


class AuthError(Exception):
    """登录失败或会话无效。对外只返回统一提示，不区分账号不存在与密码错误。"""


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class ResolvedSession:
    session_id: UUID
    user_id: UUID
    csrf_hash: str
    context: ExecutionContext | None


_MEMBERSHIPS_SQL = text(
    """
    SELECT t.id AS tenant_id, t.name AS tenant_name, p.id AS principal_id, m.role
    FROM tenant_principals p
    JOIN tenants t ON t.id = p.tenant_id AND t.status = 'active'
    JOIN memberships m ON m.principal_id = p.id AND m.tenant_id = p.tenant_id AND m.revoked_at IS NULL
    WHERE p.user_id = :uid AND p.status = 'active'
    ORDER BY t.name
    """
)


async def login(email: str, password: str) -> tuple[IssuedSession, UUID]:
    subject = email.strip().lower()
    async with runtime_tx({"app.login_subject": subject}) as s:
        row = (
            await s.execute(
                text(
                    """SELECT u.id, u.status, c.password_hash FROM users u
                       LEFT JOIN password_credentials c ON c.user_id = u.id
                       WHERE u.identity_provider = 'local' AND u.provider_subject = :sub"""
                ),
                {"sub": subject},
            )
        ).first()
        pw_hash = row.password_hash if row else None
        ok = security.verify_password(pw_hash, password)
        if not row or not ok or row.status != "active":
            raise AuthError("invalid credentials")
        user_id: UUID = row.id
        await apply_context_async(s, {"app.user_id": str(user_id)})
        if security.needs_rehash(pw_hash):
            await s.execute(
                text(
                    "UPDATE password_credentials SET password_hash = :h, updated_at = now() WHERE user_id = :u"
                ),
                {"h": security.hash_password(password), "u": user_id},
            )
        memberships = (await s.execute(_MEMBERSHIPS_SQL, {"uid": user_id})).all()
        # 只有一个企业时自动选中；多个企业时由用户选择
        tenant_id = memberships[0].tenant_id if len(memberships) == 1 else None
        principal_id = memberships[0].principal_id if len(memberships) == 1 else None
        token, csrf = security.new_token(), security.new_token()
        expires = datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)
        await s.execute(
            text(
                """INSERT INTO sessions (id, token_hash, csrf_hash, user_id, tenant_id, principal_id, expires_at)
                   VALUES (:id, :th, :ch, :u, :t, :p, :e)"""
            ),
            {
                "id": uuid4(),
                "th": security.token_digest(token),
                "ch": security.token_digest(csrf),
                "u": user_id,
                "t": tenant_id,
                "p": principal_id,
                "e": expires,
            },
        )
    return IssuedSession(token=token, csrf_token=csrf, expires_at=expires), user_id


async def resolve_session(token: str) -> ResolvedSession:
    """校验会话令牌，并重新确认企业成员资格仍然有效（撤权即时生效）。"""
    digest = security.token_digest(token)
    async with runtime_tx({"app.session_token_hash": digest}) as s:
        row = (
            await s.execute(
                text(
                    """SELECT id, user_id, csrf_hash, tenant_id, principal_id FROM sessions
                       WHERE token_hash = :th AND revoked_at IS NULL AND expires_at > now()"""
                ),
                {"th": digest},
            )
        ).first()
        if not row:
            raise AuthError("session invalid")
        await apply_context_async(s, {"app.user_id": str(row.user_id)})
        user_ok = (
            await s.execute(
                text("SELECT 1 FROM users WHERE id = :u AND status = 'active'"), {"u": row.user_id}
            )
        ).first()
        if not user_ok:
            raise AuthError("user disabled")
        ctx = None
        if row.tenant_id:
            m = (
                await s.execute(
                    text(
                        """SELECT m.role FROM memberships m
                           JOIN tenant_principals p ON p.id = m.principal_id AND p.status = 'active'
                           JOIN tenants t ON t.id = m.tenant_id AND t.status = 'active'
                           WHERE m.principal_id = :p AND m.tenant_id = :t AND m.revoked_at IS NULL"""
                    ),
                    {"p": row.principal_id, "t": row.tenant_id},
                )
            ).first()
            if m:
                ctx = ExecutionContext(
                    tenant_id=row.tenant_id, user_id=row.user_id, principal_id=row.principal_id, role=m.role
                )
        await s.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() WHERE id = :id AND last_seen_at < now() - interval '1 minute'"
            ),
            {"id": row.id},
        )
    return ResolvedSession(session_id=row.id, user_id=row.user_id, csrf_hash=row.csrf_hash, context=ctx)


async def logout(token: str) -> None:
    digest = security.token_digest(token)
    async with runtime_tx({"app.session_token_hash": digest}) as s:
        await s.execute(text("UPDATE sessions SET revoked_at = now() WHERE token_hash = :th"), {"th": digest})


async def switch_tenant(session: ResolvedSession, token: str, tenant_id: UUID) -> None:
    digest = security.token_digest(token)
    async with runtime_tx({"app.user_id": str(session.user_id), "app.session_token_hash": digest}) as s:
        memberships = (await s.execute(_MEMBERSHIPS_SQL, {"uid": session.user_id})).all()
        match = next((m for m in memberships if m.tenant_id == tenant_id), None)
        if not match:
            raise AuthError("not a member")
        await s.execute(
            text("UPDATE sessions SET tenant_id = :t, principal_id = :p WHERE id = :id"),
            {"t": tenant_id, "p": match.principal_id, "id": session.session_id},
        )


async def get_me(session: ResolvedSession) -> Me:
    async with runtime_tx({"app.user_id": str(session.user_id)}) as s:
        u = (
            await s.execute(
                text("SELECT id, display_name, provider_subject FROM users WHERE id = :u"),
                {"u": session.user_id},
            )
        ).one()
        rows = (await s.execute(_MEMBERSHIPS_SQL, {"uid": session.user_id})).all()
    memberships = [
        TenantMembership(
            tenant_id=r.tenant_id, tenant_name=r.tenant_name, principal_id=r.principal_id, role=r.role
        )
        for r in rows
    ]
    current = None
    if session.context:
        current = next((m for m in memberships if m.tenant_id == session.context.tenant_id), None)
    return Me(
        user_id=u.id,
        display_name=u.display_name,
        email=str(u.provider_subject),
        current=current,
        memberships=memberships,
    )
