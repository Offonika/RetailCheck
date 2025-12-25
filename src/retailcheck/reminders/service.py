from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from loguru import logger
from redis.asyncio import Redis

from retailcheck.config import AppConfig, load_app_config
from retailcheck.runs.repository import RunsRepository
from retailcheck.runsteps.models import RunStepRecord
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.sheets.client import SheetsClient
from retailcheck.shops.models import ShopInfo
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python

@dataclass
class ReminderSchedule:
    initial: list[int]
    repeat: int
    after_time: time | None = None
    after_interval: int | None = None


@dataclass
class ReminderState:
    last_sent: datetime | None = None
    count: int = 0


@dataclass(frozen=True)
class StepRequirement:
    code: str
    title: str
    owner_roles: set[str]
    required: bool
    phase: str


# Fixed-time reminder slots per shop/role
SHOP_FIXED_SCHEDULES: dict[str, dict[str, list[tuple[str, list[str]]]]] = {
    # Магазин 1: роли A/B разнесены
    "shop_1": {
        "opener": [
            ("09:00", ["cash_start", "photo_x_report"]),
            ("11:00", ["cash_check_1"]),
            ("11:30", ["credit_check_1"]),
            ("14:00", ["noncash_check_1"]),
            ("14:30", ["cash_check_2"]),
            ("16:30", ["credit_check_2"]),
            ("17:00", ["noncash_check_2"]),
            ("17:30", ["cash_check_3"]),
        ],
        "opener_end": [
            (
                "18:00",
                [
                    "withdrawal",
                    "pko",
                    "photo_statement",
                    "photo_acquiring",
                    "terminal_choice",
                    "photo_terminal",
                    "cash_1c",
                ],
            )
        ],
        "closer": [
            ("11:00", ["cash_check_1"]),
            ("11:30", ["credit_check_1"]),
            ("14:00", ["noncash_check_1"]),
            ("14:30", ["cash_check_2"]),
            ("17:30", ["credit_check_2"]),
            ("18:00", ["noncash_check_2"]),
            ("19:30", ["cash_check_3"]),
        ],
        "closer_end": [
            (
                "20:00",
                [
                    "withdrawal",
                    "pko",
                    "photo_statement",
                    "photo_acquiring",
                    "terminal_choice",
                    "photo_terminal",
                    "cash_1c",
                ],
            )
        ],
    },
    # Магазин 2: один сотрудник, график 10–19
    "shop_2": {
        "single": [
            ("10:00", ["cash_start", "photo_x_report"]),
            ("12:00", ["cash_check_1"]),
            ("12:30", ["credit_check_1"]),
            ("14:30", ["noncash_check_1"]),
            ("15:00", ["cash_check_2"]),
            ("17:00", ["credit_check_2"]),
            ("17:30", ["noncash_check_2"]),
            ("18:00", ["cash_check_3"]),
        ],
        "single_end": [
            (
                "19:00",
                [
                    "withdrawal",
                    "pko",
                    "photo_statement",
                    "photo_acquiring",
                    "terminal_choice",
                    "photo_terminal",
                    "cash_1c",
                ],
            )
        ],
    },
}

CLOSING_SCHEDULE = ReminderSchedule(initial=[10, 20], repeat=30)


class ReminderService:
    def __init__(
        self,
        config: AppConfig,
        sheets: SheetsClient,
        runs_repo: RunsRepository,
        runsteps_repo: RunStepsRepository,
        shops_repo: ShopsRepository,
        users_repo: UsersRepository,
        templates_repo: TemplateRepository,
        redis: Redis,
    ) -> None:
        self._config = config
        self._sheets = sheets
        self._runs_repo = runs_repo
        self._runsteps_repo = runsteps_repo
        self._shops_repo = shops_repo
        self._users_repo = users_repo
        self._templates_repo = templates_repo
        self._bot = Bot(
            token=config.bot.token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        self._user_cache: dict[str, int] | None = None
        self._redis = redis

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
                run = await self._runs_repo.get_run(shop.shop_id, today)
                await self._process_pending_steps(shop, run, user_index)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reminder failed for shop %s: %s", shop.shop_id, exc)

    async def _process_shop(
        self, mode: str, shop: ShopInfo, today: str, user_index: dict[str, int]
    ) -> None:
        title = _format_title(mode, shop)
        run = await self._runs_repo.get_run(shop.shop_id, today)
        if run and run.status in {"closed", "returned"}:
            await self._reset_reminder_state(run.run_id)
            if run.status == "closed":
                return
        # New mode: pending_steps — reminds about incomplete steps by role
        if mode == "pending_steps":
            await self._process_pending_steps(shop, run, user_index)
            return
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
                slot_id = _build_slot_id("open", shop.shop_id, run.run_id if run else None)
                if await self._should_send(slot_id):
                    delivered = await self._send_reminder(
                        self._broadcast_ids(shop, user_index), text, include_manager_group=True
                    )
                    if delivered:
                        await self._mark_sent(slot_id)
            elif open_pending:
                text = (
                    f"{title}\n" f"{shop.name}: завершите шаги открытия: {', '.join(open_pending)}."
                )
                opener_ids = self._resolve_run_user(
                    run.opener_user_id, run.opener_username, user_index
                )
                slot_id = _build_slot_id("open", shop.shop_id, run.run_id)
                if await self._should_send(slot_id):
                    delivered = await self._send_reminder(
                        opener_ids, text, include_manager_group=True
                    )
                    if delivered:
                        await self._mark_sent(slot_id)
        else:
            closer_needed = run.status in {"in_progress", "ready_to_close", "returned"}
            if closer_needed and not run.closer_user_id:
                text = f"{title}\n{shop.name}: назначьте closera для закрытия смены."
                slot_id = _build_slot_id("close", shop.shop_id, run.run_id if run else None)
                if await self._should_send(slot_id):
                    delivered = await self._send_reminder(
                        self._broadcast_ids(shop, user_index), text, include_manager_group=True
                    )
                    if delivered:
                        await self._mark_sent(slot_id)
            elif close_pending:
                text = (
                    f"{title}\n"
                    f"{shop.name}: завершите шаги закрытия/финансов: {', '.join(close_pending)}."
                )
                closer_ids = self._resolve_run_user(
                    run.closer_user_id, run.closer_username, user_index
                )
                slot_id = _build_slot_id("close", shop.shop_id, run.run_id)
                if await self._should_send(slot_id):
                    delivered = await self._send_reminder(
                        closer_ids, text, include_manager_group=True
                    )
                    if delivered:
                        await self._mark_sent(slot_id)

    async def _process_pending_steps(
        self,
        shop: ShopInfo,
        run,
        user_index: dict[str, int],
    ) -> None:
        if not run:
            logger.debug("No run for shop %s, skipping reminders", shop.shop_id)
            return
        if run.status == "closed":
            await self._reset_reminder_state(run.run_id)
            return
        if run.status == "returned":
            await self._reset_reminder_state(run.run_id)
        steps = await self._runsteps_repo.list_for_run(run.run_id)
        tz = ZoneInfo(shop.timezone)
        now_local = datetime.now(UTC).astimezone(tz)
        requirements = self._collect_required_steps(run)
        titles = {req.code: req.title for req in requirements}
        closer_day_started = any(
            (step.owner_role or "").lower() == "closer" and step.phase != "close" for step in steps
        )
        closer_enabled = closer_day_started

        schedules = SHOP_FIXED_SCHEDULES.get(shop.shop_id, {})
        if not shop.dual_cash_mode:
            await self._process_single_schedule(
                shop,
                run,
                steps,
                requirements,
                titles,
                schedules,
                now_local,
                user_index,
            )
        else:
            await self._process_dual_schedule(
                shop,
                run,
                steps,
                requirements,
                titles,
                schedules,
                now_local,
                user_index,
                closer_enabled,
            )

        # Reminders for closing phase (после start_close)
        closing_pending = self._pending_required(
            requirements, steps, role="closer", phases={"close"}
        )
        closing_start = self._closing_started_at(steps, tz)
        if closing_pending and closing_start and run.closer_user_id:
            closer_ids = self._resolve_run_user(
                run.closer_user_id, run.closer_username, user_index
            )
            await self._send_scheduled(
                slot_id=_build_slot_id("closing", shop.shop_id, run.run_id),
                schedule=CLOSING_SCHEDULE,
                start_time=closing_start,
                now_local=now_local,
                recipients=closer_ids,
                text=self._format_pending_text(shop.name, "Закрытие", closing_pending, titles),
                include_manager_group=True,
            )

    def _collect_required_steps(self, run) -> list[StepRequirement]:
        requirements: dict[str, StepRequirement] = {}
        phase_map = dict(run.template_phase_map or {})
        if not phase_map.get("open") and run.template_open_id:
            phase_map["open"] = run.template_open_id
        if not phase_map.get("close") and run.template_close_id:
            phase_map["close"] = run.template_close_id
        seen_templates: set[str] = set()
        for template_id in phase_map.values():
            if not template_id or template_id in seen_templates:
                continue
            seen_templates.add(template_id)
            try:
                template = self._templates_repo.get(template_id)
            except KeyError:
                logger.warning("Template %s not found for reminders", template_id)
                continue
            for step in template.steps:
                owner = (step.owner_role or "shared").lower()
                if owner == "both":
                    owner_roles = {"opener", "closer"}
                elif owner:
                    owner_roles = {owner}
                else:
                    owner_roles = {"shared"}
                existing = requirements.get(step.code)
                title = step.title
                required = bool(step.required)
                phase_value = getattr(template, "phase", "open") or "open"
                if existing:
                    owner_roles |= existing.owner_roles
                    required = existing.required or required
                    title = existing.title or title
                    phase_value = existing.phase
                requirements[step.code] = StepRequirement(
                    code=step.code,
                    title=title,
                    owner_roles=owner_roles,
                    required=required,
                    phase=phase_value,
                )
        return list(requirements.values())

    def _pending_required(
        self,
        requirements: list[StepRequirement],
        steps: list[RunStepRecord],
        role: str,
        phases: set[str] | None = None,
    ) -> list[str]:
        step_map: dict[tuple[str, str], list[RunStepRecord]] = {}
        for step in steps:
            owner = (step.owner_role or "shared").lower()
            step_map.setdefault((step.step_code, owner), []).append(step)
            step_map.setdefault((step.step_code, "any"), []).append(step)
        pending: list[str] = []
        for req in requirements:
            if not req.required:
                continue
            if phases and req.phase not in phases:
                continue
            owners = req.owner_roles
            if owners == {"shared"}:
                records = step_map.get((req.code, "any"), [])
                done = any(rec.status in {"ok", "skipped"} for rec in records)
                if not done:
                    pending.append(req.code)
                continue
            if role not in owners:
                continue
            records = step_map.get((req.code, role), []) or step_map.get((req.code, "shared"), [])
            done = any(rec.status in {"ok", "skipped"} for rec in records)
            if not done:
                pending.append(req.code)
        return pending

    def _closing_started_at(self, steps: list[RunStepRecord], tz: ZoneInfo) -> datetime | None:
        timestamps: list[datetime] = []
        for step in steps:
            if step.phase != "close":
                continue
            started = _parse_iso_datetime(step.started_at)
            if started:
                timestamps.append(started.astimezone(tz))
        return min(timestamps) if timestamps else None

    def _format_pending_text(
        self,
        shop_name: str,
        role_label: str,
        step_codes: list[str],
        titles: dict[str, str],
    ) -> str:
        readable = [titles.get(code, code) for code in step_codes]
        bullets = "\n".join(f"• {item}" for item in readable)
        return (
            f"📋 {shop_name}: не завершены шаги ({role_label}):\n"
            f"{bullets}\n"
            "Напоминания продолжатся, пока обязательные шаги не будут выполнены."
        )

    async def _send_scheduled(
        self,
        slot_id: str,
        schedule: ReminderSchedule,
        start_time: datetime | None,
        now_local: datetime,
        recipients: list[int],
        text: str,
        include_manager_group: bool = False,
    ) -> None:
        if not start_time or not recipients:
            return
        should_send, new_state = await self._should_send_schedule(
            slot_id, schedule, start_time, now_local
        )
        if not should_send:
            return
        delivered = await self._send_reminder(recipients, text, include_manager_group)
        if delivered:
            await self._mark_sent(slot_id, new_state)

    async def _should_send_schedule(
        self,
        slot_id: str,
        schedule: ReminderSchedule,
        start_time: datetime,
        now_local: datetime,
    ) -> tuple[bool, ReminderState]:
        state = await self._get_state(slot_id)
        if now_local < start_time:
            return False, state
        last_local = state.last_sent.astimezone(now_local.tzinfo) if state.last_sent else None
        elapsed_min = (now_local - start_time).total_seconds() / 60
        use_after_interval = schedule.after_time and now_local.time() >= schedule.after_time
        if state.count < len(schedule.initial) and not use_after_interval:
            next_due = schedule.initial[state.count]
            if elapsed_min >= next_due:
                return True, ReminderState(last_sent=now_local, count=state.count + 1)
            return False, state
        interval = (
            schedule.after_interval
            if use_after_interval and schedule.after_interval
            else schedule.repeat
        )
        if interval <= 0:
            return False, state
        minutes_since_last = _minutes_since(last_local, now_local)
        if minutes_since_last is None or minutes_since_last >= interval:
            new_count = max(state.count, len(schedule.initial)) + 1
            return True, ReminderState(last_sent=now_local, count=new_count)
        return False, state

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
        slot_id = f"dual:{shop.shop_id}:{slot}:{run.run_id}"
        if not await self._should_send(slot_id):
            return
        delivered = await self._send_reminder(
            recipients, "\n".join(lines), include_manager_group=True
        )
        if delivered:
            await self._mark_sent(slot_id)

    async def _process_single_schedule(
        self,
        shop: ShopInfo,
        run,
        steps: list[RunStepRecord],
        requirements: list[StepRequirement],
        titles: dict[str, str],
        schedules: dict[str, list[tuple[str, list[str]]]],
        now_local: datetime,
        user_index: dict[str, int],
    ) -> None:
        if not run.opener_user_id:
            return
        opener_ids = self._resolve_run_user(run.opener_user_id, run.opener_username, user_index)
        tz = ZoneInfo(shop.timezone)
        opener_slots = schedules.get("single") or schedules.get("opener") or []
        end_slots = schedules.get("single_end") or []
        pending_general = {code for code in self._pending_required(requirements, steps, "opener")}
        await self._send_fixed_slots(
            shop,
            run,
            slots=opener_slots,
            role_label="A",
            pending_codes=pending_general,
            titles=titles,
            recipients=opener_ids,
            now_local=now_local,
            tz=tz,
            repeat_minutes=None,
        )
        await self._send_fixed_slots(
            shop,
            run,
            slots=end_slots,
            role_label="A",
            pending_codes=pending_general,
            titles=titles,
            recipients=opener_ids,
            now_local=now_local,
            tz=tz,
            repeat_minutes=10,
            slot_suffix="end",
        )

    async def _process_dual_schedule(
        self,
        shop: ShopInfo,
        run,
        steps: list[RunStepRecord],
        requirements: list[StepRequirement],
        titles: dict[str, str],
        schedules: dict[str, list[tuple[str, list[str]]]],
        now_local: datetime,
        user_index: dict[str, int],
        closer_enabled: bool,
    ) -> None:
        tz = ZoneInfo(shop.timezone)
        opener_slots = schedules.get("opener") or []
        opener_end_slots = schedules.get("opener_end") or []
        closer_slots = schedules.get("closer") or []
        closer_end_slots = schedules.get("closer_end") or []
        opener_pending = {
            code for code in self._pending_required(requirements, steps, "opener", phases={"open"})
        }
        closer_pending = (
            {
                code
                for code in self._pending_required(
                    requirements, steps, "closer", phases={"open", "continue"}
                )
            }
            if closer_enabled
            else set()
        )
        if run.opener_user_id:
            opener_ids = self._resolve_run_user(run.opener_user_id, run.opener_username, user_index)
            await self._send_fixed_slots(
                shop,
                run,
                slots=opener_slots,
                role_label="A",
                pending_codes=opener_pending,
                titles=titles,
                recipients=opener_ids,
                now_local=now_local,
                tz=tz,
                repeat_minutes=None,
            )
            await self._send_fixed_slots(
                shop,
                run,
                slots=opener_end_slots,
                role_label="A",
                pending_codes=opener_pending,
                titles=titles,
                recipients=opener_ids,
                now_local=now_local,
                tz=tz,
                repeat_minutes=10,
                slot_suffix="end",
            )
        if closer_pending and run.closer_user_id:
            closer_ids = self._resolve_run_user(run.closer_user_id, run.closer_username, user_index)
            await self._send_fixed_slots(
                shop,
                run,
                slots=closer_slots,
                role_label="B",
                pending_codes=closer_pending,
                titles=titles,
                recipients=closer_ids,
                now_local=now_local,
                tz=tz,
                repeat_minutes=None,
            )
            await self._send_fixed_slots(
                shop,
                run,
                slots=closer_end_slots,
                role_label="B",
                pending_codes=closer_pending,
                titles=titles,
                recipients=closer_ids,
                now_local=now_local,
                tz=tz,
                repeat_minutes=10,
                slot_suffix="end",
            )

    async def _send_fixed_slots(
        self,
        shop: ShopInfo,
        run,
        slots: list[tuple[str, list[str]]],
        role_label: str,
        pending_codes: set[str],
        titles: dict[str, str],
        recipients: list[int],
        now_local: datetime,
        tz: ZoneInfo,
        repeat_minutes: int | None,
        slot_suffix: str | None = None,
        include_manager_group: bool = True,
    ) -> None:
        if not recipients:
            return
        for slot_time_str, codes in slots:
            slot_time = _parse_hh_mm(slot_time_str, tz, now_local.date())
            if not slot_time or now_local < slot_time:
                continue
            missing = [code for code in codes if code in pending_codes]
            if not missing:
                continue
            slot_id = f"fixed:{run.run_id}:{role_label}:{slot_time_str}"
            if slot_suffix:
                slot_id += f":{slot_suffix}"
            if not await self._should_send_fixed(slot_id, now_local, repeat_minutes):
                continue
            text = self._format_pending_text(shop.name, role_label, missing, titles)
            delivered = await self._send_reminder(
                recipients,
                text,
                include_manager_group=include_manager_group,
            )
            if delivered:
                await self._mark_sent(slot_id, ReminderState(last_sent=now_local, count=1))

    async def _should_send_fixed(
        self, slot_id: str, now_local: datetime, repeat_minutes: int | None
    ) -> bool:
        state = await self._get_state(slot_id)
        if state.last_sent is None:
            return True
        if repeat_minutes is None:
            return False
        delta_minutes = _minutes_since(state.last_sent, now_local)
        return delta_minutes is None or delta_minutes >= repeat_minutes

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
    ) -> bool:
        manager_ids = self._config.notifications.manager_chat_ids
        delivered = await self._send_to_ids(direct_ids, text)
        if include_manager_group and manager_ids:
            delivered_group = await self._send_to_ids(manager_ids, text)
            delivered = delivered or delivered_group
        elif not delivered and manager_ids:
            delivered = await self._send_to_ids(manager_ids, text)
        return delivered

    async def _send_to_ids(self, chat_ids: list[int], text: str) -> bool:
        delivered = False
        for chat_id in dict.fromkeys(chat_ids):
            try:
                await self._bot.send_message(chat_id=chat_id, text=text)
                delivered = True
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send reminder to %s: %s", chat_id, exc)
        return delivered

    async def _get_state(self, slot_id: str) -> ReminderState:
        key = f"reminder_state:{slot_id}"
        raw = await self._redis.get(key)
        if not raw:
            return ReminderState()
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return ReminderState()
        last_sent = (
            _parse_iso_datetime(payload.get("last_sent")) if isinstance(payload, dict) else None
        )
        count = 0
        if isinstance(payload, dict):
            try:
                count = int(payload.get("count", 0) or 0)
            except (TypeError, ValueError):
                count = 0
        return ReminderState(last_sent=last_sent, count=count)

    async def _should_send(self, slot_id: str) -> bool:
        """Compatibility helper for legacy modes (send once if not sent yet)."""
        state = await self._get_state(slot_id)
        return state.last_sent is None

    async def _mark_sent(self, slot_id: str, state: ReminderState | None = None) -> None:
        key = f"reminder_state:{slot_id}"
        payload_state = state or ReminderState(last_sent=datetime.now(UTC), count=1)
        payload = {
            "last_sent": payload_state.last_sent.isoformat() if payload_state.last_sent else "",
            "count": payload_state.count,
        }
        await self._redis.setex(key, 3 * 24 * 3600, json.dumps(payload))

    async def _reset_reminder_state(self, run_id: str) -> None:
        pattern = f"reminder_state:*:{run_id}"
        async for key in self._redis.scan_iter(match=pattern):
            await self._redis.delete(key)


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


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string, return None if invalid."""
    if not value:
        return None
    try:
        # Handle various ISO formats
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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
    templates_repo = TemplateRepository(sheets)
    redis = Redis.from_url(config.redis.url)
    service = ReminderService(
        config,
        sheets,
        runs_repo,
        runsteps_repo,
        shops_repo,
        users_repo,
        templates_repo,
        redis,
    )
    try:
        await service.run_mode(mode, shop_ids)
    finally:
        await service.close()
        await redis.close()


def _build_slot_id(mode: str, shop_id: str, run_id: str | None) -> str:
    base_run = run_id or "no_run"
    return f"{mode}:{shop_id}:{base_run}"


def _minutes_since(previous: datetime | None, now: datetime) -> float | None:
    if not previous:
        return None
    return (now - previous).total_seconds() / 60


def _parse_hh_mm(value: str, tz: ZoneInfo, current_date: date) -> datetime | None:
    try:
        hour, minute = value.split(":")
        return datetime(
            year=current_date.year,
            month=current_date.month,
            day=current_date.day,
            hour=int(hour),
            minute=int(minute),
            tzinfo=tz,
        )
    except Exception:
        return None


def _to_local(timestamp: str | None, tz: ZoneInfo) -> datetime | None:
    parsed = _parse_iso_datetime(timestamp)
    if not parsed:
        return None
    return parsed.astimezone(tz)
