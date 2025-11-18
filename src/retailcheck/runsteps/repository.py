from __future__ import annotations

import asyncio

from retailcheck.runsteps.models import RUN_STEP_HEADERS, RunStepRecord
from retailcheck.sheets.client import SheetsClient


class RunStepsRepository:
    """Store RunSteps sheet in Google Sheets."""

    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def list_for_run(self, run_id: str) -> list[RunStepRecord]:
        return await asyncio.to_thread(self._list_sync, run_id)

    async def upsert(self, records: list[RunStepRecord]) -> None:
        await asyncio.to_thread(self._upsert_sync, records)

    # ---- sync helpers --------------------------------------------------

    def _list_sync(self, run_id: str) -> list[RunStepRecord]:
        values = self._sheets.read("RunSteps!A2:M")
        result = []
        for row in values:
            if not row or not row[0]:
                continue
            if row[0] == run_id:
                result.append(RunStepRecord.from_row(row))
        return result

    def _upsert_sync(self, records: list[RunStepRecord]) -> None:
        current_rows = self._sheets.read("RunSteps!A2:M")
        existing: dict[tuple[str, str, str], RunStepRecord] = {}
        for row in current_rows:
            if not row or not row[0]:
                continue
            record = RunStepRecord.from_row(row)
            existing[(record.run_id, record.phase, record.step_code)] = record

        for record in records:
            existing[(record.run_id, record.phase, record.step_code)] = record

        rows = [RUN_STEP_HEADERS]
        for record in existing.values():
            rows.append(record.to_row())

        self._sheets.clear("RunSteps")
        self._sheets.write("RunSteps!A1", rows)
