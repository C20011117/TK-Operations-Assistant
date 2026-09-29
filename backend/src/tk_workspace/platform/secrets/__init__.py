"""密钥存取：Windows 凭据管理器（keyring，底层 DPAPI）。

密钥不进数据库、不进日志、不进任何文件；接口只返回“是否已配置”和末 4 位。
"""

import sys

import keyring
from keyring.errors import PasswordDeleteError

from tk_workspace.config import KEYRING_SERVICE


def _ensure_backend() -> None:
    """打包后的程序里 keyring 的自动发现（依赖包元数据）可能失效，这里显式指定 Windows 凭据管理器。"""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from keyring.backends.Windows import WinVaultKeyring

        if not isinstance(keyring.get_keyring(), WinVaultKeyring):
            keyring.set_keyring(WinVaultKeyring())


FASTMOSS_API_KEY = "fastmoss_api_key"
LLM_API_KEY = "llm_api_key"
PII_DATA_KEY = "pii_data_key"  # 个人数据字段加密用的数据密钥（base64），见 platform/crypto
KNOWN = (FASTMOSS_API_KEY, LLM_API_KEY, PII_DATA_KEY)


def get_secret(name: str) -> str:
    assert name in KNOWN, name
    _ensure_backend()
    return keyring.get_password(KEYRING_SERVICE, name) or ""


def set_secret(name: str, value: str) -> None:
    assert name in KNOWN, name
    _ensure_backend()
    keyring.set_password(KEYRING_SERVICE, name, value)


def delete_secret(name: str) -> None:
    assert name in KNOWN, name
    _ensure_backend()
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except PasswordDeleteError:
        pass


def hint(value: str) -> str | None:
    """只显示末 4 位，便于用户确认填的是哪把 Key。"""
    if not value:
        return None
    return "…" + value[-4:] if len(value) > 8 else "已配置"
