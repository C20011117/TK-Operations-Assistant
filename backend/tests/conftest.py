"""测试配置：每个测试使用独立的临时数据目录和 SQLite；密钥使用内存 keyring，绝不触碰真实凭据管理器。"""

import os

os.environ["TKWS_APP_ENV"] = "test"

import keyring  # noqa: E402
import pytest  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

TEST_TOKEN = "test-launch-token-0123456789abcdef"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring(request):
    if request.node.get_closest_marker("live"):
        yield None
        return
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(previous)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """独立数据目录 + 已迁移的数据库。"""
    from tk_workspace.config import get_settings
    from tk_workspace.platform.db.engine import reset_engine
    from tk_workspace.platform.db.migrate import upgrade_to_head

    monkeypatch.setenv("TKWS_DATA_DIR", str(tmp_path / "TKWorkspace"))
    get_settings.cache_clear()
    reset_engine()
    upgrade_to_head()
    yield get_settings().data_dir
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient

    from tk_workspace.api.main import create_app
    from tk_workspace.config import get_settings

    app = create_app(get_settings(), token=TEST_TOKEN)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c
