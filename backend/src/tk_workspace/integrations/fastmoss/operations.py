"""FastMoss 固定操作注册表（ADR-05）：内部操作名 → MCP 工具、参数校验、预计扣费。

模型不能自造工具名、参数或地址；新增能力只能在这里登记。M0 只登记免费的余额查询，
creator.search 等在 M2 加入。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tk_workspace.config import get_settings
from tk_workspace.integrations.fastmoss.mcp_client import FastMossMCPClient, ToolResult


class NoParams(BaseModel):
    pass


@dataclass(frozen=True)
class ProviderOperation:
    name: str
    tool: str
    params_model: type[BaseModel]
    credit_cost_estimate: Callable[[BaseModel], int]


OPERATIONS: dict[str, ProviderOperation] = {
    "account.credits": ProviderOperation(
        name="account.credits",
        tool="credit_usage_summary",
        params_model=NoParams,
        credit_cost_estimate=lambda _p: 0,  # 2026-09-29 实测不扣费
    ),
}


def client_from_settings() -> FastMossMCPClient:
    from tk_workspace.modules.settings.service import get_fastmoss_key

    return FastMossMCPClient(get_settings().fastmoss_mcp_url, get_fastmoss_key())


def execute(
    op_name: str, params: dict[str, Any] | None = None, client: FastMossMCPClient | None = None
) -> ToolResult:
    op = OPERATIONS[op_name]
    validated = op.params_model.model_validate(params or {})
    owns = client is None
    c = client or client_from_settings()
    try:
        return c.call_tool(op.tool, validated.model_dump(exclude_none=True))
    finally:
        if owns:
            c.close()
