import httpx
import pytest

from tests.conftest import TEST_PASSWORD
from tk_workspace.api.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(seeded):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _login(c: httpx.AsyncClient, email: str) -> dict:
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


async def test_login_sets_httponly_session_and_returns_tenant(client):
    me = await _login(client, "bd.a@demo.local")
    assert me["current"]["role"] == "bd"
    assert me["current"]["tenant_name"] == "演示企业"
    set_cookie = " ".join(client.cookies.keys())
    assert "tkws_session" in set_cookie and "tkws_csrf" in set_cookie


async def test_wrong_password_is_generic(client):
    r = await client.post("/api/v1/auth/login", json={"email": "bd.a@demo.local", "password": "nope"})
    assert r.status_code == 401
    r2 = await client.post("/api/v1/auth/login", json={"email": "nobody@demo.local", "password": "nope"})
    assert r2.status_code == 401
    assert r.json() == r2.json()


async def test_requires_login(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/markets")).status_code == 401


async def test_mutation_requires_csrf(client):
    me = await _login(client, "bd.a@demo.local")
    tid = me["current"]["tenant_id"]
    r = await client.post(
        f"/api/v1/tenants/{tid}/system/jobs", json={}, headers={"Idempotency-Key": "k-12345678"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


async def test_path_tenant_is_not_authorization(client, seeded):
    await _login(client, "bd.a@demo.local")
    other = seeded["bd.c@other.local"]["tenant_id"]
    r = await client.get(f"/api/v1/tenants/{other}/system/jobs")
    assert r.status_code == 404


async def test_logout_revokes_session(client):
    await _login(client, "bd.a@demo.local")
    csrf = client.cookies.get("tkws_csrf")
    token = client.cookies.get("tkws_session")
    r = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    client.cookies.set("tkws_session", token)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
