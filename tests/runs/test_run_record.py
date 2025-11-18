from retailcheck.runs.models import RunRecord


def test_from_row_with_template_phase_map():
    row = [
        "run_1",
        "2025-01-01",
        "shop_1",
        "closed",
        "100",
        "@opener",
        "2025-01-01T08:00:00Z",
        "200",
        "@closer",
        "2025-01-01T22:00:00Z",
        "200",
        "open_v1",
        "close_v1",
        '{"open":"open_v1","close":"close_v1"}',
        "10.0",
        "Комментарий",
        "3",
        "2025-01-01T08:00:00Z",
        "2025-01-01T22:05:00Z",
    ]
    record = RunRecord.from_row(row)
    assert record.version == 3
    assert record.template_phase_map["open"] == "open_v1"
    assert record.delta_rub == "10.0"
    assert record.current_active_user_id == "200"


def test_from_row_legacy_without_phase_map():
    row = [
        "run_legacy",
        "2025-02-01",
        "shop_2",
        "in_progress",
        "101",
        "@user",
        "2025-02-01T08:10:00Z",
        "",
        "",
        "",
        "open_v1",
        "close_v1",
        "5.5",
        "Legacy comment",
        "2",
        "2025-02-01T08:00:00Z",
        "",
    ]
    record = RunRecord.from_row(row)
    assert record.template_phase_map.get("open") == "open_v1"
    assert record.template_phase_map.get("close") == "close_v1"
    assert record.delta_rub == "5.5"
    assert record.version == 2
    assert record.current_active_user_id is None


def test_from_row_phase_map_no_active():
    row = [
        "run_mid",
        "2025-03-01",
        "shop_3",
        "opened",
        "201",
        "@user",
        "2025-03-01T08:00:00Z",
        "",
        "",
        "",
        "open_v1",
        "close_v1",
        '{"open":"open_v1","close":"close_v1"}',
        "",
        "",
        "1",
        "2025-03-01T07:50:00Z",
        "",
    ]
    record = RunRecord.from_row(row)
    assert record.template_phase_map["close"] == "close_v1"
    assert record.current_active_user_id is None
