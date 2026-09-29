import pytest

from tk_workspace.modules.campaigns.markets import (
    MarketNotQueryable,
    fastmoss_region_for,
    get_market,
    list_markets,
)

pytestmark = pytest.mark.integration


async def test_europe_catalog(seeded):
    markets = await list_markets("europe")
    codes = [m.market_code for m in markets]
    assert codes == ["UK", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "PL", "PT", "IE"]


async def test_uk_maps_to_gb(seeded):
    uk = await get_market("uk")
    assert uk and uk.settlement_currency == "GBP" and uk.default_time_zone == "Europe/London"
    assert fastmoss_region_for(uk) == "GB"


async def test_ireland_is_manual_only(seeded):
    ie = await get_market("IE")
    assert ie and ie.data_status == "manual_import_only"
    with pytest.raises(MarketNotQueryable):
        fastmoss_region_for(ie)


async def test_currency_unknown_markets(seeded):
    by_code = {m.market_code: m for m in await list_markets("europe")}
    for code in ("NL", "BE", "AT", "PL"):
        assert by_code[code].data_status == "queryable_currency_unknown"
        assert by_code[code].probe_currency is None
    assert by_code["PL"].settlement_currency == "PLN"
