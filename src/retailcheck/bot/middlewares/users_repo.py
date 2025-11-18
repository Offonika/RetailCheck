from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from retailcheck.users.repository import UsersRepository


class UsersRepositoryMiddleware(BaseMiddleware):
    def __init__(self, users_repository: UsersRepository) -> None:
        super().__init__()
        self._users_repository = users_repository

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["users_repository"] = self._users_repository
        return await handler(event, data)
