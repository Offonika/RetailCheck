import pytest

from retailcheck.runsteps.models import RunStepRecord
from retailcheck.runsteps.repository import RunStepsRepository


class FakeSheets:
    def __init__(self) -> None:
        self.data = {"RunSteps": []}

    def read(self, sheet_range: str):
        sheet = sheet_range.split("!")[0]
        rows = self.data.get(sheet, [])
        return rows

    def clear(self, sheet_name: str):
        self.data[sheet_name] = []

    def write(self, sheet_range: str, values):
        sheet = sheet_range.split("!")[0]
        self.data[sheet] = values[1:]  # drop header for simplicity


@pytest.mark.asyncio
async def test_upsert_and_list():
    sheets = FakeSheets()
    repo = RunStepsRepository(sheets)  # type: ignore[arg-type]
    record = RunStepRecord(run_id="run_1", phase="open", step_code="cash")
    await repo.upsert([record])
    rows = await repo.list_for_run("run_1")
    assert len(rows) == 1
    assert rows[0].step_code == "cash"
