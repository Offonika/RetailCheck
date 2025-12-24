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
        values = self._sheets.read("RunSteps!A2:N")
        result = []
        for row in values:
            if not row or not row[0]:
                continue
            if row[0] == run_id:
                result.append(RunStepRecord.from_row(row))
        return result

    def _upsert_sync(self, records: list[RunStepRecord]) -> None:
        # WARNING: This method uses read-modify-write pattern without locking.
        # Concurrent updates to different records may cause data loss.
        # For production, consider adding Redis locks or using optimistic locking.
        current_rows = self._sheets.read("RunSteps!A2:N")
        existing: dict[tuple[str, str, str, str], RunStepRecord] = {}
        for row in current_rows:
            if not row or not row[0]:
                continue
            record = RunStepRecord.from_row(row)
            # Normalize owner_role to prevent None from causing key mismatches
            owner_role = (record.owner_role or "shared").lower()
            existing[(record.run_id, record.phase, record.step_code, owner_role)] = record

        for record in records:
            # Normalize owner_role consistently
            owner_role = (record.owner_role or "shared").lower()
            key = (record.run_id, record.phase, record.step_code, owner_role)
            existing[key] = record

        rows = [RUN_STEP_HEADERS]
        for record in existing.values():
            rows.append(record.to_row())

        self._sheets.clear("RunSteps")
        self._sheets.write("RunSteps!A1", rows)
