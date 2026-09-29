"""数据库请求上下文。

每个事务开始时用参数化 set_config(..., true) 设置 app.* 事务级变量，RLS 策略据此判断可见行。
`true` 表示只在当前事务内有效，连接池复用不会带入上一个请求的上下文。
这些变量只能由平台代码设置，业务代码和模型不能拼接任意 SQL。
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Role = Literal["admin", "bd", "viewer"]

CONTEXT_KEYS = (
    "app.tenant_id",
    "app.user_id",
    "app.principal_id",
    "app.claim_job_id",
    "app.login_subject",
    "app.session_token_hash",
)


@dataclass(frozen=True)
class ExecutionContext:
    """当前请求或后台任务的执行身份（见 docs/architecture/01-system-design.md 2.1）。"""

    tenant_id: UUID
    user_id: UUID | None
    principal_id: UUID
    role: Role | None
    principal_kind: Literal["user", "service"] = "user"
    trace_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def as_settings(self) -> dict[str, str]:
        values = {
            "app.tenant_id": str(self.tenant_id),
            "app.principal_id": str(self.principal_id),
        }
        if self.user_id:
            values["app.user_id"] = str(self.user_id)
        values.update(self.extra)
        return values


SET_CONFIG_SQL = "SELECT set_config(:k, :v, true)"
