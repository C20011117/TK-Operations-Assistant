"""个人数据字段加密（AES-256-GCM）。

- 数据密钥 32 字节，首次使用时生成，存 Windows 凭据管理器；数据库、文件和日志里都没有。
- 密文格式 ``v1:`` + base64url(nonce 12 字节 + 密文)；附加数据绑定“表.列:行 ID”，
  密文被复制到别的行或别的列都会解密失败。
- 数据库文件单独拷到别的电脑上读不出这些字段；凭据丢失时也无法恢复（需要重新填写）。
"""

import base64
import hashlib
import hmac
import json
import os
import threading
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tk_workspace.platform import secrets

PREFIX = "v1:"
_lock = threading.Lock()


class PiiKeyError(Exception):
    """数据密钥缺失或与密文不匹配。消息里不含任何个人数据。"""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(create: bool) -> bytes:
    with _lock:
        raw = secrets.get_secret(secrets.PII_DATA_KEY)
        if not raw:
            if not create:
                raise PiiKeyError(
                    "本机没有个人数据密钥（可能换了电脑或 Windows 用户），已保存的收件信息无法读取"
                )
            raw = _b64e(AESGCM.generate_key(bit_length=256))
            secrets.set_secret(secrets.PII_DATA_KEY, raw)
        key = _b64d(raw)
        if len(key) != 32:
            raise PiiKeyError("个人数据密钥格式不正确")
        return key


def _aad(table: str, column: str, row_id: str) -> bytes:
    return f"{table}.{column}:{row_id}".encode()


def encrypt(value: Any, *, table: str, column: str, row_id: str) -> str:
    """加密任意可 JSON 序列化的值。"""
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    nonce = os.urandom(12)
    ct = AESGCM(_key(create=True)).encrypt(nonce, data, _aad(table, column, row_id))
    return PREFIX + _b64e(nonce + ct)


def decrypt(token: str, *, table: str, column: str, row_id: str) -> Any:
    if not token.startswith(PREFIX):
        raise PiiKeyError("密文格式不正确")
    blob = _b64d(token[len(PREFIX) :])
    try:
        data = AESGCM(_key(create=False)).decrypt(blob[:12], blob[12:], _aad(table, column, row_id))
    except InvalidTag as e:
        raise PiiKeyError("个人数据密钥与密文不匹配，无法读取") from e
    return json.loads(data)


def keyed_hash(value: Any) -> str:
    """HMAC-SHA256（数据密钥派生的子密钥）：用于确认哈希，不能靠哈希反推个人数据。"""
    sub = hmac.new(_key(create=True), b"tkws-payload-hash-v1", hashlib.sha256).digest()
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(sub, data, hashlib.sha256).hexdigest()
