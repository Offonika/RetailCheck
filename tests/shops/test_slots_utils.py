from retailcheck.shops.utils import _parse_slots


def test_parse_slots_json_dict():
    raw = '{"dual_checks":["12:00","13:30"],"custom":["08:45"]}'
    slots = _parse_slots(raw)
    assert slots["dual_checks"] == ["12:00", "13:30"]
    assert slots["custom"] == ["08:45"]


def test_parse_slots_list():
    raw = '["10:00","11:30"]'
    slots = _parse_slots(raw)
    assert slots == {"custom": ["10:00", "11:30"]}


def test_parse_slots_csv():
    raw = "10:00, 11:30 , "
    slots = _parse_slots(raw)
    assert slots == {"custom": ["10:00", "11:30"]}
