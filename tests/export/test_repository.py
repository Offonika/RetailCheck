from retailcheck.export.models import ExportRecord


def test_export_record_to_row():
    class DummyRun:
        run_id = "run"
        date = "2025-02-01"
        shop_id = "shop_1"
        status = "closed"
        comment = ""
        opener_user_id = "100"
        opener_username = "user1"
        opener_at = "2025-02-01T10:00:00Z"
        closer_user_id = "200"
        closer_username = "user2"
        closer_at = "2025-02-01T22:00:00Z"

    record = ExportRecord.from_summary(
        DummyRun(),
        [],
        [],
        delta_total=0.0,
        shop_name="Магазин 1",
        cash_total="1000.00",
        noncash_total="500.00",
        delta_comment="Комментарий",
    )
    row = record.to_row()
    assert row[0] == record.export_id
    assert row[3] == "shop_1"
