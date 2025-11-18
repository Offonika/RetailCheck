from __future__ import annotations

from datetime import date

from aiogram import Bot
from loguru import logger

from retailcheck.config import AppConfig, load_app_config
from retailcheck.runs.repository import RunsRepository
from retailcheck.runsteps.models import RunStepRecord
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.models import ShopInfo
from retailcheck.shops.repository import ShopsRepository
from retailcheck.users.repository import UsersRepository


class ReminderService:
    def __init__(
        self,
        config: AppConfig,
        sheets: SheetsClient,
        runs_repo: RunsRepository,
        runsteps_repo: RunStepsRepository,
        shops_repo: ShopsRepository,
        users_repo: UsersRepository,
    ) -> None:
        self._config = config
        self._sheets = sheets
        self._runs_repo = runs_repo
        self._runsteps_repo = runsteps_repo
        self._shops_repo = shops_repo
        self._users_repo = users_repo
        self._bot = Bot(token=config.bot.token, parse_mode="HTML")
        self._user_cache: dict[str, int] | None = None

    async def run_mode(self, mode: str, shop_ids: list[str] | None = None) -> None:
        shops = await self._shops_repo.list_active()
        if shop_ids:
            target = {sid.lower() for sid in shop_ids}
            shops = [shop for shop in shops if shop.shop_id.lower() in target]
        if not shops:
            logger.info("No shops configured for mode %s", mode)
            return
        today = date.today().isoformat()
        user_index = await self._build_user_index()
        for shop in shops:
            try:
                await self._process_shop(mode, shop, today, user_index)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reminder failed for shop %s: %s", shop.shop_id, exc)

    async def _process_shop(
        self, mode: str, shop: ShopInfo, today: str, user_index: dict[str, int]
    ) -> None:
        title = _format_title(mode, shop)
        run = await self._runs_repo.get_run(shop.shop_id, today)
        if mode.startswith("dual:"):
            slot = mode.split(":", 1)[1] if ":" in mode else ""
            await self._process_dual_slot(slot, shop, run, title, user_index)
            return
        if not run:
            text = f"{title}\nМагазин {shop.name} ({shop.shop_id}) ещё не начал смену."
            await self._send_reminder(
                self._broadcast_ids(shop, user_index), text, include_manager_group=True
            )
            return
        if run.status == "closed":
            return

        steps = await self._runsteps_repo.list_for_run(run.run_id)
        open_pending = [
            step.step_code
            for step in steps
            if step.phase == "open" and step.status not in {"ok", "skipped"}
        ]
        close_pending = [
            step.step_code
            for step in steps
            if step.phase in {"close", "finance"} and step.status not in {"ok", "skipped"}
        ]

        if mode == "open":
            if not run.opener_user_id:
                text = f"{title}\n{shop.name}: требуется назначить opener перед началом смены."
                await self._send_reminder(
                    self._broadcast_ids(shop, user_index), text, include_manager_group=True
                )
            elif open_pending:
                text = (
                    f"{title}\n" f"{shop.name}: завершите шаги открытия: {', '.join(open_pending)}."
                )
                opener_ids = self._resolve_run_user(
                    run.opener_user_id, run.opener_username, user_index
                )
                await self._send_reminder(opener_ids, text, include_manager_group=True)
        else:
            closer_needed = run.status in {"in_progress", "ready_to_close", "returned"}
            if closer_needed and not run.closer_user_id:
                text = f"{title}\n{shop.name}: назначьте closera для закрытия смены."
                await self._send_reminder(
                    self._broadcast_ids(shop, user_index), text, include_manager_group=True
                )
            elif close_pending:
                text = (
                    f"{title}\n"
                    f"{shop.name}: завершите шаги закрытия/финансов: {', '.join(close_pending)}."
                )
                closer_ids = self._resolve_run_user(
                    run.closer_user_id, run.closer_username, user_index
                )
                await self._send_reminder(closer_ids, text, include_manager_group=True)

    async def _process_dual_slot(
        self,
        slot: str,
        shop: ShopInfo,
        run,
        title: str,
        user_index: dict[str, int],
    ) -> None:
        if not shop.dual_cash_mode:
            return
        if not run:
            text = (
                f"{title}\n{shop.name}: смена ещё не создана, напоминание кассы A/B "
                f"({slot or 'слот'}) отправлено всем сотрудникам."
            )
            await self._send_reminder(
                self._broadcast_ids(shop, user_index), text, include_manager_group=True
            )
            return
        if run.status == "closed":
            return
        steps = await self._runsteps_repo.list_for_run(run.run_id)
        opener_pending = _pending_steps(steps, {"opener", "shared"})
        closer_pending = _pending_steps(steps, {"closer", "shared"})
        lines = [
            title,
            f"{shop.name}: дневная сверка ({slot or 'слот'})",
        ]
        if opener_pending:
            lines.append("• Opener: завершите шаги " + ", ".join(opener_pending))
        if closer_pending:
            lines.append("• Closer: завершите шаги " + ", ".join(closer_pending))
        if not opener_pending and not closer_pending:
            lines.append("• Проверьте кассу и подтвердите значения.")
        recipients: list[int] = []
        if run.opener_user_id:
            recipients += self._resolve_run_user(
                run.opener_user_id,
                run.opener_username,
                user_index,
            )
        if run.closer_user_id:
            recipients += self._resolve_run_user(
                run.closer_user_id,
                run.closer_username,
                user_index,
            )
        if not recipients:
            recipients = self._broadcast_ids(shop, user_index)
        await self._send_reminder(recipients, "\n".join(lines), include_manager_group=True)

    async def close(self) -> None:
        await self._bot.session.close()

    async def _build_user_index(self) -> dict[str, int]:
        if self._user_cache is not None:
            return self._user_cache
        records = await self._users_repo.list_active()
        index: dict[str, int] = {}
        for record in records:
            if record.username and record.tg_id:
                index[record.username.lower().lstrip("@")] = record.tg_id
        self._user_cache = index
        return index

    def _broadcast_ids(self, shop, user_index: dict[str, int]) -> list[int]:
        usernames = shop.employee_usernames + shop.manager_usernames
        return self._resolve_usernames(usernames, user_index)

    def _resolve_usernames(self, usernames: list[str], user_index: dict[str, int]) -> list[int]:
        ids: list[int] = []
        for username in usernames:
            key = username.lower().lstrip("@")
            if key in user_index:
                ids.append(user_index[key])
        return list(dict.fromkeys(ids))

    def _resolve_run_user(
        self,
        user_id: str | None,
        username: str | None,
        user_index: dict[str, int],
    ) -> list[int]:
        if username:
            key = username.lower().lstrip("@")
            chat_id = user_index.get(key)
            if chat_id:
                return [chat_id]
        if user_id:
            try:
                return [int(user_id)]
            except ValueError:
                return []
        return []

    async def _send_reminder(
        self, direct_ids: list[int], text: str, include_manager_group: bool
    ) -> None:
        manager_ids = self._config.notifications.manager_chat_ids
        delivered = await self._send_to_ids(direct_ids, text)
        if include_manager_group and manager_ids:
            await self._send_to_ids(manager_ids, text)
        elif not delivered and manager_ids:
            await self._send_to_ids(manager_ids, text)

    async def _send_to_ids(self, chat_ids: list[int], text: str) -> bool:
        delivered = False
        for chat_id in dict.fromkeys(chat_ids):
            try:
                await self._bot.send_message(chat_id=chat_id, text=text)
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send reminder to %s: %s", chat_id, exc)
        return delivered


def _format_title(mode: str, shop: ShopInfo) -> str:
    if mode == "open":
        return f"Напоминание перед открытием (план {shop.open_time}, tz {shop.timezone}, −15 мин)"
    if mode == "close":
        return f"Напоминание перед закрытием (план {shop.close_time}, tz {shop.timezone}, −30 мин)"
    if mode.startswith("dual:"):
        slot = mode.split(":", 1)[1] if ":" in mode else ""
        return f"Дневная сверка ({slot or 'слот'}, tz {shop.timezone})"
    return f"Напоминание ({shop.name})"


def _pending_steps(steps: list[RunStepRecord], owner_roles: set[str]) -> list[str]:
    roles = {role.lower() for role in owner_roles}
    result = []
    for step in steps:
        role = (step.owner_role or "shared").lower()
        if role not in roles:
            continue
        if step.status not in {"ok", "skipped"}:
            result.append(step.step_code)
    return result


async def run_reminders(mode: str, shop_ids: list[str] | None = None) -> None:
    config = load_app_config()
    sheets = SheetsClient(
        spreadsheet_id=config.google.sheets_id,
        service_account_file=config.google.service_account_json,
    )
    runs_repo = RunsRepository(sheets)
    runsteps_repo = RunStepsRepository(sheets)
    shops_repo = ShopsRepository(sheets)
    users_repo = UsersRepository(sheets)
    service = ReminderService(config, sheets, runs_repo, runsteps_repo, shops_repo, users_repo)
    try:
        await service.run_mode(mode, shop_ids)
    finally:
        await service.close()
