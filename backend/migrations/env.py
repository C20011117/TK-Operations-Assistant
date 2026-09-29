"""Alembic 环境（SQLite）。数据库地址来自 alembic Config（启动迁移时设置）或当前运行配置。"""

from alembic import context
from sqlalchemy import create_engine, pool

from tk_workspace.config import get_settings


def _url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or get_settings().db_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, render_as_batch=True, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
