from tk_workspace.modules.settings.service import LLMConfig
from tk_workspace.platform.llm import gateway


def test_not_configured_does_not_call_network():
    res = gateway.check(LLMConfig())
    assert res.status == "not_configured"


def test_configured_detection():
    cfg = LLMConfig(base_url="https://api.example.invalid/v1", api_key="k", model_matching="m")
    assert cfg.configured
    assert "k" not in cfg.model_dump_json()
