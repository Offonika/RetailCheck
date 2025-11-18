from __future__ import annotations

import asyncio

from retailcheck.export.models import ExportRecord
from retailcheck.sheets.client import SheetsClient


class ExportRepository:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def append(self, record: ExportRecord) -> None:
        await asyncio.to_thread(self._append_sync, record)

    def _append_sync(self, record: ExportRecord) -> None:
        rows = self._sheets.read("Export!A2:W")
        rows.append(record.to_row())
        header = [
            [
                "export_id",
                "period_start",
                "period_end",
                "shop_id",
                "shop_name",
                "run_id",
                "run_date",
                "status",
                "opener_user_id",
                "opener_username",
                "opener_at",
                "closer_user_id",
                "closer_username",
                "closer_at",
                "totals_json",
                "cash_total",
                "noncash_total",
                "delta_total",
                "delta_comment",
                "comment",
                "attachments_summary",
                "audit_link",
                "generated_at",
            ]
        ]
        self._sheets.clear("Export")
        self._sheets.write("Export!A1", header + rows)
