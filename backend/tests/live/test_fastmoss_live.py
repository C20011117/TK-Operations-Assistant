"""真实 FastMoss 连通测试（默认跳过）。Key 读取自 Windows 凭据管理器。运行：uv run pytest -m live tests/live"""

import pytest

from tk_workspace.integrations.fastmoss import operations
from tk_workspace.modules.settings.service import get_fastmoss_key

pytestmark = pytest.mark.live


def test_credit_summary_is_free():
    if not get_fastmoss_key():
        pytest.skip("凭据管理器中没有 FastMoss Key")
    res = operations.execute("account.credits")
    assert res.charge is not None and res.charge.credit_cost == 0
    assert "balance" in res.data
