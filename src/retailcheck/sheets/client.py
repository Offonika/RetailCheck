from __future__ import annotations

import socket
import time
from collections import Counter
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


RETRYABLE_IO_ERRORS = (
    BrokenPipeError,
    TimeoutError,
    ConnectionError,
    socket.timeout,
)


class SheetsClient:
    """Thin wrapper over Google Sheets API with retry logic."""

    def __init__(
        self,
        spreadsheet_id: str,
        service_account_file: Path,
        *,
        service: Any | None = None,
        error_notifier: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._error_notifier = error_notifier
        self._error_counts: Counter[str] = Counter()
        if service is not None:
            self._service = service
            return
        if CredentialsClass is None or BuildCallable is None:
            raise ImportError(
                "Google Sheets client requires `google-api-python-client` and "
                "`google-auth` packages. Install project dependencies to use SheetsClient."
            )
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
                self._record_error("http_error")
                logger.warning(
                    "Google Sheets HTTP error (%s/%s): %s",
                    attempt,
                    MAX_RETRIES,
                    err,
                )
                if attempt == MAX_RETRIES:
                    self._emit_alert("http_error", err)
                    raise
                time.sleep(delay)
                delay = min(delay * 2, DEFAULT_TIMEOUT)
            except RETRYABLE_IO_ERRORS as err:
                self._record_error("io_error")
                logger.warning(
                    "Google Sheets transport error (%s/%s): %s",
                    attempt,
                    MAX_RETRIES,
                    err,
                )
                if attempt == MAX_RETRIES:
                    self._emit_alert("io_error", err)
                    raise
                time.sleep(delay)
                delay = min(delay * 2, DEFAULT_TIMEOUT)
            except Exception as err:  # pragma: no cover - unexpected failures
                self._record_error("unexpected_error")
                logger.exception("Unexpected Sheets error on attempt %s: %s", attempt, err)
                self._emit_alert("unexpected_error", err)
                raise

    def get_error_stats(self) -> Counter[str]:
        return Counter(self._error_counts)

    def _record_error(self, kind: str) -> None:
        self._error_counts[kind] += 1

    def _emit_alert(self, kind: str, err: Exception) -> None:
        logger.error("Sheets error alert [%s]: %s", kind, err)
        if not self._error_notifier:
            return
        try:
            self._error_notifier(kind, err)
        except Exception as notifier_err:  # pragma: no cover - logging only
            logger.error("Sheets error notifier failed: %s", notifier_err)
