"""真实 FastMoss 连通测试（默认跳过）。运行：uv run pytest -m live tests/live"""

import pytest

from tk_workspace.config import get_settings
from tk_workspace.integrations.fastmoss import operations

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not get_settings().fastmoss_mcp_api_key.get_secret_value(), reason="未配置 FASTMOSS_MCP_API_KEY"
)
def test_credit_summary_is_free():
    res = operations.execute("account.credits")
    assert res.charge is not None and res.charge.credit_cost == 0
    assert "balance" in res.data
