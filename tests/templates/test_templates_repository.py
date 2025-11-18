from __future__ import annotations

import pytest

from retailcheck.sheets.client import SheetsClient
from retailcheck.templates.repository import TemplateRepository


class FakeSheets(SheetsClient):
    def __init__(self, data: dict[str, list[list[str]]]) -> None:
        self._data = data

    def read(self, sheet_range: str):
        sheet_name = sheet_range.split("!")[0]
        return self._data.get(sheet_name, [])

    def write(self, *args, **kwargs):
        raise NotImplementedError

    def clear(self, *args, **kwargs):
        raise NotImplementedError


@pytest.fixture
def repo():
    data = {
        "Templates": [
            ["opening_v1", "Открытие", "1", "open", "TRUE", "Описание"],
            ["closing_v1", "Закрытие", "1", "close", "TRUE", "Описание"],
        ],
        "TemplateSteps": [
            ["opening_v1", "1", "cash_open", "Касса", "number", "TRUE", "", "", ""],
            ["closing_v1", "1", "cash_close", "Касса 19:00", "number", "TRUE", "", "", ""],
        ],
    }
    return TemplateRepository(FakeSheets(data))


def test_get_template(repo: TemplateRepository):
    tmpl = repo.get("opening_v1")
    assert tmpl.name == "Открытие"
    assert tmpl.phase == "open"
    assert tmpl.steps[0].code == "cash_open"


def test_list_by_phase(repo: TemplateRepository):
    closes = repo.list_by_phase("close")
    assert len(closes) == 1
    assert closes[0].template_id == "closing_v1"
