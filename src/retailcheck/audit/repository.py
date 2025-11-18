from __future__ import annotations

import asyncio

from retailcheck.audit.models import AuditRecord
from retailcheck.sheets.client import SheetsClient


class AuditRepository:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def append(self, record: AuditRecord) -> None:
        await asyncio.to_thread(self._append_sync, record)

    # --- sync -----------------------------------------------------------

    def _append_sync(self, record: AuditRecord) -> None:
        rows = self._sheets.read("Audit!A2:F")
        rows.append(record.to_row())
        header = [["ts", "user_id", "action", "entity", "entity_id", "details"]]
        self._sheets.clear("Audit")
        self._sheets.write("Audit!A1", header + rows)
