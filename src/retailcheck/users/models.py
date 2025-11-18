from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    tg_id: int
    username: str | None
    full_name: str
    role: str
    shops: list[str]
    is_active: bool

    def can_work_in_shop(self, shop_id: str) -> bool:
        return shop_id in self.shops
