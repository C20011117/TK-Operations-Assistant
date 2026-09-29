"""迁移与市场目录。"""

import pytest

from tk_workspace.config import get_settings
from tk_workspace.modules.campaigns import markets
from tk_workspace.platform.db import migrate


def test_eleven_european_markets(client):
    data = client.get("/api/v1/markets").json()
    assert [m["market_code"] for m in data] == [
        "UK",
        "DE",
        "FR",
        "IT",
        "ES",
        "NL",
        "BE",
        "AT",
        "PL",
        "PT",
        "IE",
    ]
    uk = data[0]
    assert uk["fastmoss_region"] == "GB" and uk["settlement_currency"] == "GBP"
    assert uk["content_languages"] == ["en"]


def test_ireland_is_manual_import_only(data_dir):
    ie = markets.get_market("IE")
    assert ie is not None and ie.data_status == "manual_import_only"
    with pytest.raises(markets.MarketNotQueryable):
        markets.fastmoss_region_for(ie)
    assert markets.fastmoss_region_for(markets.get_market("uk")) == "GB"


def test_upgrade_is_idempotent(data_dir):
    assert migrate.upgrade_to_head() == "up_to_date"


def test_backup_and_prune(data_dir):
    s = get_settings()
    for _ in range(migrate.KEEP_BACKUPS + 2):
        assert migrate.backup(s, reason="test") is not None
    assert len(list(s.backups_dir.glob("app-*.db"))) <= migrate.KEEP_BACKUPS


def test_data_lives_in_data_dir(data_dir):
    assert get_settings().db_path.is_file()
    assert str(get_settings().db_path).startswith(str(data_dir))
