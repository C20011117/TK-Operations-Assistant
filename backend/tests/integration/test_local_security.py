"""本机接口保护：令牌、Host 检查、CORS。"""

from fastapi.testclient import TestClient

from tk_workspace.api.main import create_app
from tk_workspace.config import get_settings

from ..conftest import TEST_TOKEN


def _raw(data_dir, base_url="http://127.0.0.1:8765") -> TestClient:
    return TestClient(create_app(get_settings(), token=TEST_TOKEN), base_url=base_url)


def test_no_token_is_rejected(data_dir):
    r = _raw(data_dir).get("/api/v1/system/health")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_rejected(data_dir):
    r = _raw(data_dir).get("/api/v1/markets", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_foreign_host_is_rejected_even_with_token(data_dir):
    """DNS 重绑定：攻击者域名解析到 127.0.0.1 时 Host 头是攻击者域名。"""
    c = _raw(data_dir, base_url="http://evil.example:8765")
    r = c.get("/api/v1/system/health", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert r.status_code == 403


def test_valid_token_passes(client):
    r = client.get("/api/v1/system/health")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] == "ok"


def test_cors_allows_tauri_origin_only(data_dir):
    c = _raw(data_dir)
    pre = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = c.options("/api/v1/markets", headers={"Origin": "http://tauri.localhost", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    bad = c.options("/api/v1/markets", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers


def test_production_requires_launch_token():
    import pytest

    from tk_workspace.api.main import resolve_token
    from tk_workspace.config import Settings

    with pytest.raises(RuntimeError):
        resolve_token(Settings(app_env="production", launch_token=""))
