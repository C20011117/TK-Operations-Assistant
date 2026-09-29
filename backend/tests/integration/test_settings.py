"""设置：密钥只进凭据管理器，数据库与接口中都没有明文。"""

from tk_workspace.config import KEYRING_SERVICE, get_settings
from tk_workspace.platform import secrets

LLM_KEY = "sk-test-SECRET-llm-key-9876"
FM_KEY = "fm_sk_test_SECRET_fastmoss_5555"


def test_llm_settings_roundtrip_without_leaking_key(client, memory_keyring):
    r = client.put(
        "/api/v1/settings/llm",
        json={"base_url": "https://api.example.invalid/v1/", "model_matching": "m-1", "api_key": LLM_KEY},
    )
    assert r.status_code == 200, r.text
    view = client.get("/api/v1/settings").json()
    assert view["llm"]["base_url"] == "https://api.example.invalid/v1"
    assert view["llm"]["configured"] is True
    assert view["llm"]["api_key_hint"] == "…9876"
    assert LLM_KEY not in r.text and LLM_KEY not in client.get("/api/v1/settings").text
    assert memory_keyring.store[(KEYRING_SERVICE, secrets.LLM_API_KEY)] == LLM_KEY


def test_keys_never_written_to_data_dir(client):
    client.put(
        "/api/v1/settings/llm",
        json={"base_url": "https://x.invalid/v1", "model_matching": "m", "api_key": LLM_KEY},
    )
    client.put("/api/v1/settings/fastmoss", json={"api_key": FM_KEY})
    for f in get_settings().data_dir.rglob("*"):
        if f.is_file():
            blob = f.read_bytes()
            assert b"SECRET" not in blob, f


def test_saving_without_key_keeps_existing_key(client, memory_keyring):
    client.put(
        "/api/v1/settings/llm",
        json={"base_url": "https://x.invalid/v1", "model_matching": "m", "api_key": LLM_KEY},
    )
    client.put("/api/v1/settings/llm", json={"base_url": "https://y.invalid/v1", "model_matching": "m2"})
    assert memory_keyring.store[(KEYRING_SERVICE, secrets.LLM_API_KEY)] == LLM_KEY
    client.put(
        "/api/v1/settings/llm",
        json={"base_url": "https://y.invalid/v1", "model_matching": "m2", "api_key": ""},
    )
    assert (KEYRING_SERVICE, secrets.LLM_API_KEY) not in memory_keyring.store


def test_invalid_base_url_rejected(client):
    r = client.put("/api/v1/settings/llm", json={"base_url": "file:///etc/passwd", "model_matching": "m"})
    assert r.status_code == 422


def test_checks_report_not_configured_without_network(client):
    r = client.post("/api/v1/settings/checks").json()
    assert r["llm"]["status"] == "not_configured"
    assert r["fastmoss"]["status"] == "not_configured"


def test_fastmoss_key_set_and_delete(client, memory_keyring):
    client.put("/api/v1/settings/fastmoss", json={"api_key": FM_KEY})
    assert client.get("/api/v1/settings").json()["fastmoss"]["api_key_hint"] == "…5555"
    client.put("/api/v1/settings/fastmoss", json={"api_key": ""})
    assert client.get("/api/v1/settings").json()["fastmoss"]["configured"] is False
