"""Run APScheduler reminders with interval schedule (pending steps)."""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from retailcheck.alerts.delta import run_delta_alerts
from retailcheck.config import load_app_config
from retailcheck.reminders.service import run_reminders

PENDING_INTERVAL_MIN = 5


async def main() -> None:
    config = load_app_config()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_reminders,
        "interval",
        minutes=PENDING_INTERVAL_MIN,
        args=["pending_steps", None],
        id="pending_steps",
        replace_existing=True,
    )
    delta_interval = int(config.alerts.delta_cooldown_sec / 60) or 5
    scheduler.add_job(run_delta_alerts, "interval", minutes=delta_interval)
    scheduler.start()
    print(
        f"Reminder scheduler started: pending_steps каждые {PENDING_INTERVAL_MIN} мин, "
        f"дельта-алерты каждые {delta_interval} мин",
    )
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
