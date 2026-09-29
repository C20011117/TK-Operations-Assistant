from decimal import Decimal

from tk_workspace.platform.db.types import DecimalText, utcnow_iso


def test_decimal_text_keeps_precision():
    t = DecimalText()
    assert t.process_bind_param(Decimal("0.1") + Decimal("0.2"), None) == "0.3"
    assert t.process_result_value("12345678901234567890.123456", None) == Decimal(
        "12345678901234567890.123456"
    )
    assert t.process_bind_param(None, None) is None


def test_utc_iso_sorts_lexicographically():
    a = utcnow_iso()
    b = utcnow_iso()
    assert a.endswith("Z") and a <= b
