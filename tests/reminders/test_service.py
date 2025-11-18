from retailcheck.shops.utils import _parse_slots


def test_parse_slots():
    assert _parse_slots("11:00, 16:00 ,19:00") == {"custom": ["11:00", "16:00", "19:00"]}  # noqa: SLF001
    assert _parse_slots("") == {}
