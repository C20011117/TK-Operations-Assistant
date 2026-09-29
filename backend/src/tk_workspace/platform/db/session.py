"""引擎与会话工厂。API 用 asyncpg；Worker、分发器、迁移用同步 psycopg。

规则：每个请求 / 任务拥有独立 Session，禁止全局共享 AsyncSession（ADR-02）。
"""

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from tk_workspace.config import get_settings
from tk_workspace.platform.db.context import SET_CONFIG_SQL


@lru_cache
def runtime_async_engine() -> AsyncEngine:
    return create_async_engine(get_settings().runtime_async_dsn, pool_pre_ping=True, pool_size=10)


@lru_cache
def runtime_sync_engine() -> Engine:
    return create_engine(get_settings().runtime_sync_dsn, pool_pre_ping=True, pool_size=5)


@lru_cache
def dispatcher_sync_engine() -> Engine:
    return create_engine(get_settings().dispatcher_sync_dsn, pool_pre_ping=True, pool_size=2)


async def apply_context_async(session: AsyncSession, values: Mapping[str, str]) -> None:
    for k, v in values.items():
        await session.execute(text(SET_CONFIG_SQL), {"k": k, "v": v})


def apply_context_sync(session: Session, values: Mapping[str, str]) -> None:
    for k, v in values.items():
        session.execute(text(SET_CONFIG_SQL), {"k": k, "v": v})


@asynccontextmanager
async def runtime_tx(values: Mapping[str, str] | None = None) -> AsyncIterator[AsyncSession]:
    """开启一个运行角色事务，并设置 RLS 上下文。退出时提交，异常时回滚。"""
    factory = async_sessionmaker(runtime_async_engine(), expire_on_commit=False)
    async with factory() as session, session.begin():
        if values:
            await apply_context_async(session, values)
        yield session


@contextmanager
def runtime_tx_sync(values: Mapping[str, str] | None = None) -> Iterator[Session]:
    factory = sessionmaker(runtime_sync_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        if values:
            apply_context_sync(session, values)
        yield session


@contextmanager
def dispatcher_tx() -> Iterator[Session]:
    factory = sessionmaker(dispatcher_sync_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        yield session
