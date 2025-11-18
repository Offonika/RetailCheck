from __future__ import annotations

from aiogram import Bot
from loguru import logger
from redis.asyncio import Redis

from retailcheck.config import AppConfig, load_app_config
from retailcheck.runs.repository import RunsRepository
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.repository import ShopsRepository


class DeltaAlertService:
    def __init__(
        self,
        config: AppConfig,
        runs_repo: RunsRepository,
        runsteps_repo: RunStepsRepository,
        shops_repo: ShopsRepository,
        redis: Redis,
    ) -> None:
        self._config = config
        self._runs_repo = runs_repo
        self._runsteps_repo = runsteps_repo
        self._shops_repo = shops_repo
        self._redis = redis
        self._bot = Bot(token=config.bot.token, parse_mode="HTML")

    async def run(self) -> None:
        threshold = self._config.alerts.delta_threshold_rub
        shops = {shop.shop_id: shop for shop in await self._shops_repo.list_active()}
        runs = await self._runs_repo.list_runs()
        for run in runs:
            if run.shop_id not in shops:
                continue
            if run.status == "closed":
                await self._reset_flag(run.run_id)
                continue
            steps = await self._runsteps_repo.list_for_run(run.run_id)
            total_delta = 0.0
            for step in steps:
                raw_delta = step.delta_number
                if raw_delta is None or raw_delta == "":
                    continue
                try:
                    total_delta += float(raw_delta)
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid delta '%s' for step %s in run %s",
                        raw_delta,
                        step.step_code,
                        run.run_id,
                    )
                    continue
            if abs(total_delta) >= threshold:
                await self._maybe_alert(run.run_id, shops[run.shop_id], run, total_delta, steps)
            else:
                await self._reset_flag(run.run_id)

    async def close(self) -> None:
        await self._bot.session.close()

    async def _maybe_alert(self, run_id: str, shop, run, delta: float, steps) -> None:
        key = f"delta_alert:{run_id}"
        already = await self._redis.get(key)
        if already:
            return
        if not self._config.notifications.manager_chat_ids:
            logger.warning("Delta alert triggered but MANAGER_NOTIFY_CHAT_IDS is empty.")
            return
        role_deltas: dict[str, float] = {}
        for step in steps:
            raw_delta = step.delta_number
            if raw_delta is None or raw_delta == "":
                continue
            role = (step.owner_role or "shared").lower()
            try:
                value = float(raw_delta)
            except (TypeError, ValueError):
                continue
            role_deltas[role] = role_deltas.get(role, 0.0) + value
        text = (
            f"⚠️ Дельта по смене {shop.name} ({shop.shop_id}) за {run.date}: {delta:+.2f} ₽.\n"
            f"Статус: {run.status}. Проверьте шаги и комментарии."
        )
        if shop.dual_cash_mode and role_deltas:
            role, value = max(role_deltas.items(), key=lambda item: abs(item[1]))
            text += f"\nОсновная касса: {role} (Δ={value:+.2f})"
        success = False
        for chat_id in self._config.notifications.manager_chat_ids:
            try:
                await self._bot.send_message(chat_id=chat_id, text=text)
                success = True
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send delta alert to %s: %s", chat_id, exc)
        if success:
            await self._redis.setex(
                key,
                self._config.alerts.delta_cooldown_sec,
                "1",
            )

    async def _reset_flag(self, run_id: str) -> None:
        key = f"delta_alert:{run_id}"
        await self._redis.delete(key)


async def run_delta_alerts() -> None:
    config = load_app_config()
    sheets = SheetsClient(
        spreadsheet_id=config.google.sheets_id,
        service_account_file=config.google.service_account_json,
    )
    runs_repo = RunsRepository(sheets)
    runsteps_repo = RunStepsRepository(sheets)
    shops_repo = ShopsRepository(sheets)
    redis = Redis.from_url(config.redis.url)
    service = DeltaAlertService(config, runs_repo, runsteps_repo, shops_repo, redis)
    try:
        await service.run()
    finally:
        await service.close()
        await redis.close()
