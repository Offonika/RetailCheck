from __future__ import annotations

import asyncio

from retailcheck.attachments.models import ATTACHMENT_HEADERS, AttachmentRecord
from retailcheck.sheets.client import SheetsClient


class AttachmentRepository:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def list_for_run(self, run_id: str) -> list[AttachmentRecord]:
        return await asyncio.to_thread(self._list_sync, run_id)

    async def add(self, record: AttachmentRecord) -> None:
        await asyncio.to_thread(self._add_sync, record)

    # --- sync helpers --------------------------------------------------

    def _list_sync(self, run_id: str) -> list[AttachmentRecord]:
        values = self._sheets.read("Attachments!A2:E")
        items = []
        for row in values:
            if not row or row[0] != run_id:
                continue
            items.append(AttachmentRecord.from_row(row))
        return items

    def _add_sync(self, record: AttachmentRecord) -> None:
        current_rows = self._sheets.read("Attachments!A2:E")
        current_rows.append(record.to_row())
        rows = [ATTACHMENT_HEADERS]
        rows.extend(current_rows)
        self._sheets.clear("Attachments")
        self._sheets.write("Attachments!A1", rows)
