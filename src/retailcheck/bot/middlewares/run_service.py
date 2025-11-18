from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from retailcheck.runs.service import RunService


class RunServiceMiddleware(BaseMiddleware):
    def __init__(self, run_service: RunService) -> None:
        super().__init__()
        self._run_service = run_service

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["run_service"] = self._run_service
        return await handler(event, data)
