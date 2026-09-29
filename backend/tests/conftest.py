"""测试配置：集成测试使用独立数据库 <DB_NAME>_test，每次测试会话重建结构并写入种子数据。"""

import os

os.environ["APP_ENV"] = "test"
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "tk_workspace_test")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "Test-Password-2026!"


@pytest.fixture(scope="session")
def migrated_db():
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    from tk_workspace.scripts.seed_dev import seed

    return seed(password=TEST_PASSWORD)


@pytest.fixture(scope="session")
def seeded(migrated_db):
    return migrated_db
