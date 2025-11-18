from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from retailcheck.shops.repository import ShopsRepository


class ShopsRepositoryMiddleware(BaseMiddleware):
    def __init__(self, shops_repository: ShopsRepository) -> None:
        super().__init__()
        self._shops_repository = shops_repository

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["shops_repository"] = self._shops_repository
        return await handler(event, data)
