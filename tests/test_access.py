from datetime import datetime

from src.access import UNPAID_MESSAGE, access_allowed


def test_free_period_and_owner_then_paid_windows():
    rules = {
        "paid": {"approved": True, "start": "2026-11-08 09:00", "end": "2026-12-01 18:00"},
        "off": {"approved": False, "start": "", "end": ""},
    }
    during_free = datetime(2026, 10, 15, 12, 0)
    after_free = datetime(2026, 11, 8, 12, 0)
    assert access_allowed("nobody", during_free, rules)[0] is True
    assert access_allowed("hvineypal", datetime(2027, 1, 1), rules)[0] is True
    assert access_allowed("paid", after_free, rules)[0] is True
    assert access_allowed("nobody", after_free, rules) == (False, UNPAID_MESSAGE)
    assert access_allowed("off", after_free, rules)[0] is False
    assert access_allowed("paid", datetime(2026, 12, 2, 9, 0), rules)[0] is False
    assert "haven't paid" in UNPAID_MESSAGE
    assert "contact the owner" in UNPAID_MESSAGE
