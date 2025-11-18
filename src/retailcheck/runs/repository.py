from __future__ import annotations

import asyncio

from retailcheck.runs.models import RUN_HEADERS, RunRecord
from retailcheck.sheets.client import SheetsClient


class RunsRepository:
    """Simple Sheets-backed repository for Runs sheet."""

    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def get_run(self, shop_id: str, date: str) -> RunRecord | None:
        return await asyncio.to_thread(self._get_run_sync, shop_id, date)

    async def save_run(self, record: RunRecord) -> None:
        await asyncio.to_thread(self._save_run_sync, record)

    async def list_runs(self) -> list[RunRecord]:
        return await asyncio.to_thread(self._list_runs_sync)

    # --- sync helpers -----------------------------------------------------

    def _list_runs_sync(self) -> list[RunRecord]:
        values = self._sheets.read("Runs!A2:S")
        records = []
        for row in values:
            if not row or not any(row):
                continue
            records.append(RunRecord.from_row(row))
        return records

    def _get_run_sync(self, shop_id: str, date: str) -> RunRecord | None:
        for record in self._list_runs_sync():
            if record.shop_id == shop_id and record.date == date:
                return record
        return None

    def _save_run_sync(self, record: RunRecord) -> None:
        records = self._list_runs_sync()
        updated = False
        for idx, existing in enumerate(records):
            if existing.run_id == record.run_id:
                records[idx] = record
                updated = True
                break
        if not updated:
            records.append(record)
        self._write_all(records)

    def _write_all(self, records: list[RunRecord]) -> None:
        rows = [RUN_HEADERS]
        rows.extend(record.to_row() for record in records)
        self._sheets.clear("Runs")
        self._sheets.write("Runs!A1", rows)
