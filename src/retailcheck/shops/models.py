from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShopInfo:
    shop_id: str
    name: str
    timezone: str
    open_time: str
    close_time: str
    manager_usernames: list[str]
    employee_usernames: list[str]
    reminder_slots: dict[str, list[str]]
    allow_anyone: bool
    dual_cash_mode: bool = False
