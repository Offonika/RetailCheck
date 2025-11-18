from __future__ import annotations

import asyncio
import os

from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.models import ShopInfo
from retailcheck.shops.utils import _normalize_time, _parse_slots, _parse_usernames

DEFAULT_TIMEZONE = os.getenv("TZ", "Europe/Moscow")
DEFAULT_OPEN_TIME = os.getenv("SHOP_DEFAULT_OPEN_TIME", "09:00")
DEFAULT_CLOSE_TIME = os.getenv("SHOP_DEFAULT_CLOSE_TIME", "21:00")
DEFAULT_REMINDER_SLOTS: list[str] = []
ALLOW_ANYONE_DEFAULT = os.getenv("ALLOW_ANYONE_DEFAULT", "FALSE").upper() == "TRUE"


class ShopsRepository:
    """Lightweight reader for Shops sheet (shop_id + name)."""

    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def list_active(self) -> list[ShopInfo]:
        return await asyncio.to_thread(self._list_active_sync)

    # ---- sync helpers -------------------------------------------------

    def _list_active_sync(self) -> list[ShopInfo]:
        rows = self._sheets.read("Shops!A2:L")
        shops: list[ShopInfo] = []
        for row in rows:
            if not row or not row[0].strip():
                continue
            is_active = row[10].strip().upper() if len(row) > 10 and row[10] else "TRUE"
            if is_active == "FALSE":
                continue
            name = row[1].strip() if len(row) > 1 and row[1].strip() else row[0].strip()
            timezone = row[2].strip() if len(row) > 2 and row[2].strip() else DEFAULT_TIMEZONE
            open_time = _normalize_time(row[3] if len(row) > 3 else "", DEFAULT_OPEN_TIME)
            close_time = _normalize_time(row[4] if len(row) > 4 else "", DEFAULT_CLOSE_TIME)
            manager_usernames = _parse_usernames(row[5] if len(row) > 5 else "")
            employee_usernames = _parse_usernames(row[6] if len(row) > 6 else "")
            slots_raw = row[7] if len(row) > 7 else ""
            reminder_slots = _parse_slots(slots_raw) if slots_raw.strip() else {}
            allow_anyone = (
                (row[8].strip().upper() == "TRUE")
                if len(row) > 8 and row[8]
                else ALLOW_ANYONE_DEFAULT
            )
            dual_cash_mode = row[9].strip().upper() == "TRUE" if len(row) > 9 and row[9] else False
            shops.append(
                ShopInfo(
                    shop_id=row[0].strip(),
                    name=name,
                    timezone=timezone,
                    open_time=open_time,
                    close_time=close_time,
                    manager_usernames=manager_usernames,
                    employee_usernames=employee_usernames,
                    reminder_slots=reminder_slots,
                    allow_anyone=allow_anyone,
                    dual_cash_mode=dual_cash_mode,
                )
            )
        return shops
