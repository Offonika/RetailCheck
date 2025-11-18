import json
from types import SimpleNamespace

import pytest

from retailcheck.attachments.models import AttachmentRecord
from retailcheck.export.utils import append_export_record
from retailcheck.runsteps.models import RunStepRecord


class FakeExportRepository:
    def __init__(self) -> None:
        self.records = []

    async def append(self, record):
        self.records.append(record)


class FakeRunStepsRepository:
    def __init__(self, steps):
        self._steps = steps
        self.requested_run_id = None

    async def list_for_run(self, run_id: str):
        self.requested_run_id = run_id
        return list(self._steps)


class FakeAttachmentsRepository:
    def __init__(self, attachments):
        self._attachments = attachments
        self.requested_run_id = None

    async def list_for_run(self, run_id: str):
        self.requested_run_id = run_id
        return list(self._attachments)


class FakeShopsRepository:
    async def list_active(self):
        return [SimpleNamespace(shop_id="shop_1", name="Магазин 1")]


class DummyRun:
    run_id = "run_001"
    date = "2025-01-01"
    shop_id = "shop_1"
    status = "closed"
    opener_user_id = "100"
    opener_username = "@opener"
    opener_at = "2025-01-01T08:00:00Z"
    closer_user_id = "200"
    closer_username = "@closer"
    closer_at = "2025-01-01T22:00:00Z"
    comment = "Комментарий по смене"


@pytest.mark.asyncio
async def test_append_export_record_populates_extended_fields():
    steps = [
        RunStepRecord(
            run_id="run_001",
            phase="close",
            step_code="close_cash_end",
            owner_role="closer",
            value_number="1200.00",
            delta_number="30",
            comment="касса +30",
            status="ok",
        ),
        RunStepRecord(
            run_id="run_001",
            phase="finance",
            step_code="fin_sberbank_sum",
            owner_role="closer",
            value_number="300.00",
            delta_number="-5",
            comment="эквайринг -5",
            status="ok",
        ),
        RunStepRecord(
            run_id="run_001",
            phase="finance",
            step_code="fin_z_photo",
            owner_role="closer",
            value_text="photo:file_z",
            status="ok",
        ),
        RunStepRecord(
            run_id="run_001",
            phase="finance",
            step_code="fin_receipts_photo",
            owner_role="closer",
            value_text="photo:file_receipt",
            status="ok",
        ),
    ]
    attachments = [
        AttachmentRecord(
            run_id="run_001",
            step_code="fin_z_photo",
            telegram_file_id="file_z",
            kind="z_report",
        ),
        AttachmentRecord(
            run_id="run_001",
            step_code="fin_receipts_photo",
            telegram_file_id="file_receipt",
            kind="pos_receipt",
        ),
    ]
    export_repo = FakeExportRepository()
    steps_repo = FakeRunStepsRepository(steps)
    attachments_repo = FakeAttachmentsRepository(attachments)
    shops_repo = FakeShopsRepository()

    record, total_delta = await append_export_record(
        DummyRun(),
        steps_repo,
        attachments_repo,
        export_repo,
        shops_repository=shops_repo,
    )

    assert total_delta == pytest.approx(25.0)
    assert record.shop_name == "Магазин 1"
    assert record.cash_total == "1200.00"
    assert record.noncash_total == "300.00"
    assert record.delta_comment == "касса +30; эквайринг -5"
    assert "closer:fin_receipts_photo:pos_receipt=file_receipt" in record.attachments_summary
    assert "closer:fin_z_photo:z_report=file_z" in record.attachments_summary
    totals = json.loads(record.totals_json)
    assert totals["closer"]["close_cash_end"] == "1200.00"
    assert totals["closer"]["fin_sberbank_sum"] == "300.00"
    assert export_repo.records and export_repo.records[0] is record
