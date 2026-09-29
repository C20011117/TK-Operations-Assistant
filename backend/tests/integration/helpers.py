from typing import Any

from sqlalchemy import text

from tk_workspace.platform.db.session import runtime_tx_sync


def rows_as(ctx: dict[str, str], sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """以运行角色 + 指定 RLS 上下文执行查询。"""
    with runtime_tx_sync(ctx) as s:
        return list(s.execute(text(sql), params or {}).all())


def ctx_of(seeded: dict, email: str) -> dict[str, str]:
    u = seeded[email]
    return {
        "app.tenant_id": u["tenant_id"],
        "app.principal_id": u["principal_id"],
        "app.user_id": u["user_id"],
    }
