"""Alembic 环境：迁移只用表所有者 app_owner 执行，运行角色不参与 DDL。"""

from alembic import context
from sqlalchemy import create_engine, pool

from tk_workspace.config import get_settings


def run_migrations_offline() -> None:
    context.configure(url=get_settings().owner_sync_dsn, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().owner_sync_dsn, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
