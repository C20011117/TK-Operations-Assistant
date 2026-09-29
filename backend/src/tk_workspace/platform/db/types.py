"""SQLite 专用列类型。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import String, TypeDecorator


class DecimalText(TypeDecorator[Decimal]):
    """金额：以十进制字符串保存，读写都是 Decimal。SQLite 的 NUMERIC 会变成浮点数，不能用于金额。"""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


def utcnow_iso() -> str:
    """UTC 时间统一存为 ISO 8601 文本（毫秒精度，带 Z），可按字典序比较。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
