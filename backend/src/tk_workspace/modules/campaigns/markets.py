"""站点目录（全局、只读）。任务只保存 market_code，调用供应方时再映射地区码。"""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from tk_workspace.platform.db.session import runtime_tx

DataStatus = Literal["queryable", "queryable_currency_unknown", "platform_unconfirmed", "manual_import_only"]


class Market(BaseModel):
    market_code: str
    name_zh: str
    name_en: str
    region_group: str
    settlement_currency: str
    default_time_zone: str
    content_languages: list[str]
    fastmoss_region: str | None
    data_status: DataStatus
    last_probe_total: int | None
    probe_currency: str | None
    notes: str


class MarketNotQueryable(Exception):
    def __init__(self, market_code: str, reason: str) -> None:
        super().__init__(f"{market_code}: {reason}")
        self.market_code = market_code
        self.reason = reason


_SQL = """SELECT market_code, name_zh, name_en, region_group, settlement_currency, default_time_zone,
                 content_languages, fastmoss_region, data_status, last_probe_total, probe_currency, notes
          FROM markets"""


async def list_markets(region_group: str | None = None) -> list[Market]:
    sql = _SQL + (" WHERE region_group = :g" if region_group else "") + " ORDER BY sort_order"
    async with runtime_tx() as s:
        rows = (await s.execute(text(sql), {"g": region_group} if region_group else {})).mappings().all()
    return [Market(**{**r, "settlement_currency": r["settlement_currency"].strip()}) for r in rows]


async def get_market(code: str) -> Market | None:
    async with runtime_tx() as s:
        r = (await s.execute(text(_SQL + " WHERE market_code = :c"), {"c": code.upper()})).mappings().first()
    return Market(**{**r, "settlement_currency": r["settlement_currency"].strip()}) if r else None


def fastmoss_region_for(market: Market) -> str:
    """站点码 → FastMoss 地区码。人工导入站点直接拒绝，不发出注定为空的请求。"""
    if market.data_status == "manual_import_only" or not market.fastmoss_region:
        raise MarketNotQueryable(market.market_code, "该站点没有 FastMoss 数据，只能人工导入")
    return market.fastmoss_region


router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=list[Market], summary="站点目录")
async def markets_index(region_group: str | None = "europe") -> list[Market]:
    return await list_markets(region_group)


@router.get("/{market_code}", response_model=Market, summary="单个站点")
async def market_detail(market_code: str) -> Market:
    m = await get_market(market_code)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": "站点不存在"})
    return m
