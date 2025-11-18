"""Run APScheduler reminders with per-shop schedule."""

from __future__ import annotations

import asyncio
from datetime import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[assignment,no-redef]

from retailcheck.alerts.delta import run_delta_alerts
from retailcheck.config import load_app_config
from retailcheck.reminders.service import run_reminders
from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.repository import (
    DEFAULT_CLOSE_TIME,
    DEFAULT_OPEN_TIME,
    ShopsRepository,
)

OPEN_OFFSET_MIN = -15
CLOSE_OFFSET_MIN = -30
DUAL_MODE_PREFIX = "dual"


def _parse_time(value: str | None, fallback: str) -> time:
    candidate = (value or "").strip()
    if not candidate:
        candidate = fallback
    try:
        hour, minute = candidate.split(":", 1)
        hour_i = max(0, min(23, int(hour)))
        minute_i = max(0, min(59, int(minute)))
        return time(hour=hour_i, minute=minute_i)
    except ValueError:
        return time.fromisoformat(fallback)


def _shift_time(base: time, delta_min: int) -> time:
    total = (base.hour * 60 + base.minute + delta_min) % (24 * 60)
    if total < 0:
        total += 24 * 60
    return time(hour=total // 60, minute=total % 60)


async def _load_shops(config) -> list:
    sheets = SheetsClient(
        spreadsheet_id=config.google.sheets_id,
        service_account_file=config.google.service_account_json,
    )
    repo = ShopsRepository(sheets)
    return await repo.list_active()


def _schedule_shop_jobs(scheduler: AsyncIOScheduler, shop) -> None:
    tz = ZoneInfo(shop.timezone)
    open_time = _shift_time(_parse_time(shop.open_time, DEFAULT_OPEN_TIME), OPEN_OFFSET_MIN)
    close_time = _shift_time(_parse_time(shop.close_time, DEFAULT_CLOSE_TIME), CLOSE_OFFSET_MIN)
    scheduler.add_job(
        run_reminders,
        "cron",
        args=["open", [shop.shop_id]],
        hour=open_time.hour,
        minute=open_time.minute,
        timezone=tz,
        id=f"open:{shop.shop_id}",
        replace_existing=True,
    )
    scheduler.add_job(
        run_reminders,
        "cron",
        args=["close", [shop.shop_id]],
        hour=close_time.hour,
        minute=close_time.minute,
        timezone=tz,
        id=f"close:{shop.shop_id}",
        replace_existing=True,
    )
    dual_slots = (shop.reminder_slots or {}).get("dual_checks", [])
    if shop.dual_cash_mode and dual_slots:
        for slot in dual_slots:
            slot_time = _parse_time(slot, DEFAULT_OPEN_TIME)
            scheduler.add_job(
                run_reminders,
                "cron",
                args=[f"{DUAL_MODE_PREFIX}:{slot}", [shop.shop_id]],
                hour=slot_time.hour,
                minute=slot_time.minute,
                timezone=tz,
                id=f"{DUAL_MODE_PREFIX}:{shop.shop_id}:{slot}",
                replace_existing=True,
            )


async def main() -> None:
    config = load_app_config()
    scheduler = AsyncIOScheduler()
    shops = await _load_shops(config)
    for shop in shops:
        _schedule_shop_jobs(scheduler, shop)
    delta_interval = int(config.alerts.delta_cooldown_sec / 60) or 5
    scheduler.add_job(run_delta_alerts, "interval", minutes=delta_interval)
    scheduler.start()
    shop_list = ", ".join(shop.shop_id for shop in shops) or "нет магазинов"
    print(
        "Reminder scheduler started for shops:",
        shop_list,
        f"(дельта-алерты каждые {delta_interval} мин)",
    )
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
