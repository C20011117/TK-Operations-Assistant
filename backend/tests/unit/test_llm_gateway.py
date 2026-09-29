from pydantic import SecretStr

from tk_workspace.config import Settings
from tk_workspace.platform.llm import gateway


def test_not_configured_does_not_call_network():
    s = Settings(llm_base_url="", llm_api_key=SecretStr(""), llm_model_matching="")
    res = gateway.check(s)
    assert res.status == "not_configured"


def test_configured_detection():
    s = Settings(
        llm_base_url="https://api.example.invalid/v1", llm_api_key=SecretStr("k"), llm_model_matching="m"
    )
    assert gateway.is_configured(s)
