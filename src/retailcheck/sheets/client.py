from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

CredentialsClass: type[Any] | None
BuildCallable: Callable[..., Any] | None
HttpError: type[Exception]
try:
    from google.oauth2.service_account import Credentials as _Credentials
    from googleapiclient.discovery import build as _build
    from googleapiclient.errors import HttpError as _HttpError
except ModuleNotFoundError:  # pragma: no cover - allows running tests without Google libs
    CredentialsClass = None
    BuildCallable = None

    class _FallbackHttpError(Exception):
        """Fallback HttpError when googleapiclient isn't installed."""

    HttpError = _FallbackHttpError
else:  # pragma: no branch
    CredentialsClass = _Credentials
    BuildCallable = _build
    HttpError = _HttpError


DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3


class SheetsClient:
    """Thin wrapper over Google Sheets API with retry logic."""

    def __init__(self, spreadsheet_id: str, service_account_file: Path) -> None:
        if CredentialsClass is None or BuildCallable is None:
            raise ImportError(
                "Google Sheets client requires `google-api-python-client` and "
                "`google-auth` packages. Install project dependencies to use SheetsClient."
            )
        self.spreadsheet_id = spreadsheet_id
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = CredentialsClass.from_service_account_file(  # type: ignore[union-attr]
            str(service_account_file),
            scopes=scopes,
        )
        self._service: Any = BuildCallable(  # type: ignore[operator]
            "sheets",
            "v4",
            credentials=creds,
            cache_discovery=False,
        )

    def read(self, sheet_range: str) -> list[list[str]]:
        response = self._execute_with_retry(
            lambda: self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=sheet_range)
            .execute()
        )
        return response.get("values", [])

    def write(
        self,
        sheet_range: str,
        values: Sequence[Sequence[str]],
        value_input_option: str = "RAW",
    ) -> None:
        body = {"values": list(values)}
        self._execute_with_retry(
            lambda: self._service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=sheet_range,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    def clear(self, sheet_range: str) -> None:
        self._execute_with_retry(
            lambda: self._service.spreadsheets()
            .values()
            .clear(spreadsheetId=self.spreadsheet_id, range=sheet_range, body={})
            .execute()
        )

    def batch_update(self, data: Sequence[dict]) -> None:
        body = {"data": list(data), "valueInputOption": "RAW"}
        self._execute_with_retry(
            lambda: self._service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=self.spreadsheet_id, body=body)
            .execute()
        )

    def _execute_with_retry(self, func: Callable[[], Any]) -> Any:
        delay = 1.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.debug("Sheets request attempt %s/%s", attempt, MAX_RETRIES)
                return func()
            except HttpError as err:
                logger.warning(
                    "Google Sheets request failed (%s/%s): %s",
                    attempt,
                    MAX_RETRIES,
                    err,
                )
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, DEFAULT_TIMEOUT)
