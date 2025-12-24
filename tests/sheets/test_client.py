import pathlib

import pytest

from retailcheck.sheets.client import MAX_RETRIES, HttpError, SheetsClient


class _DummyService:
    def __init__(self, responses):
        self._responses = list(responses)

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **_kwargs):
        return self

    def update(self, **_kwargs):
        return self

    def clear(self, **_kwargs):
        return self

    def batchUpdate(self, **_kwargs):
        return self

    def execute(self):
        if not self._responses:
            return {}
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_retry_on_broken_pipe(monkeypatch):
    dummy = _DummyService([BrokenPipeError("boom"), {"values": [["ok"]]}])
    client = SheetsClient("sheet_id", pathlib.Path("/tmp/unused.json"), service=dummy)
    values = client.read("Runs!A1:B2")
    assert values == [["ok"]]
    stats = client.get_error_stats()
    assert stats["io_error"] == 1


def _make_http_error():
    class _FakeResponse:
        status = 500
        reason = "boom"

        def getheaders(self):
            return {}

    return HttpError(resp=_FakeResponse(), content=b"boom")


def test_notifier_called_on_http_failure():
    responses = [_make_http_error() for _ in range(MAX_RETRIES)]
    dummy = _DummyService(responses)
    events: list[tuple[str, str]] = []
    client = SheetsClient(
        "sheet_id",
        pathlib.Path("/tmp/unused.json"),
        service=dummy,
        error_notifier=lambda kind, exc: events.append((kind, str(exc))),
    )
    with pytest.raises(HttpError):
        client.read("Runs!A1:B2")
    assert events
    assert events[0][0] == "http_error"
    stats = client.get_error_stats()
    assert stats["http_error"] == MAX_RETRIES
