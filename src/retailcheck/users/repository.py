from __future__ import annotations

import asyncio

from retailcheck.sheets.client import SheetsClient
from retailcheck.users.models import UserRecord


class UsersRepository:
    """Read-only repository for Users sheet."""

    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def get_by_username(self, username: str) -> UserRecord | None:
        return await asyncio.to_thread(self._get_by_username_sync, username)

    async def list_active(self) -> list[UserRecord]:
        return await asyncio.to_thread(self._list_active_sync)

    async def get_by_tg_id(self, tg_id: int) -> UserRecord | None:
        return await asyncio.to_thread(self._get_by_tg_id_sync, tg_id)

    # --- sync helpers -------------------------------------------------

    def _get_by_username_sync(self, username: str) -> UserRecord | None:
        normalized = username.lower()
        for record in self._list_all():
            if record.username and record.username.lower() == normalized:
                return record
        return None

    def _get_by_tg_id_sync(self, tg_id: int) -> UserRecord | None:
        for record in self._list_all():
            if record.tg_id == tg_id:
                return record
        return None

    def _list_active_sync(self) -> list[UserRecord]:
        return [record for record in self._list_all() if record.is_active]

    def _list_all(self) -> list[UserRecord]:
        rows = self._sheets.read("Users!A2:H")
        records: list[UserRecord] = []
        for row in rows:
            if not row or not row[0].strip():
                continue
            username = row[2].strip() if len(row) > 2 and row[2] else None
            full_name = row[3].strip() if len(row) > 3 and row[3] else (username or row[0].strip())
            shops_raw = row[5] if len(row) > 5 else ""
            shops = [shop.strip() for shop in shops_raw.split(",") if shop.strip()]
            is_active = (row[6].strip().upper() == "TRUE") if len(row) > 6 and row[6] else True
            tg_id = int(row[1]) if len(row) > 1 and row[1] else 0
            records.append(
                UserRecord(
                    user_id=row[0].strip(),
                    tg_id=tg_id,
                    username=username,
                    full_name=full_name,
                    role=row[4].strip() if len(row) > 4 else "employee",
                    shops=shops,
                    is_active=is_active,
                )
            )
        return records
