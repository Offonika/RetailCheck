from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.models import AuditRecord
from retailcheck.audit.repository import AuditRepository
from retailcheck.bot.utils.access import find_shop
from retailcheck.bot.utils.notify import broadcast_to_targets, collect_shop_chat_ids
from retailcheck.export.repository import ExportRepository
from retailcheck.export.utils import append_export_record
from retailcheck.localization import gettext as t
from retailcheck.runs.repository import RunsRepository
from retailcheck.runs.service import (
    RunAlreadyExistsError,
    RunNotFoundError,
    RunService,
    RunUser,
)
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.shops.repository import ShopsRepository
from retailcheck.users.repository import UsersRepository

router = Router()
USER_REQUIRED_TEXT = "Команда доступна только авторизованным пользователям."

HANDOVER_USAGE = t("manager.usage.handover")
CREATE_USAGE = t("manager.usage.create")
RETURN_USAGE = t("manager.usage.return")


def _role_text(role: str) -> str:
    return t(f"roles.{role}") if role in {"open", "close"} else role


def _resolve_callback_message(message: Message | InaccessibleMessage | None) -> Message | None:
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return message


@router.message(Command("manager"))
async def handle_manager_menu(
    message: Message,
    shops_repository: ShopsRepository,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return
    args = (message.text or "").split()
    target_shop = args[1].strip() if len(args) > 1 else None
    shops = await _manager_shops(user.username, shops_repository)
    if not shops:
        await message.answer(t("manager.menu.no_shops"))
        return
    if target_shop:
        shop = next((shop for shop in shops if shop.shop_id == target_shop), None)
        if not shop:
            await message.answer(t("common.shop_not_found", shop_id=target_shop))
            return
        await _send_manager_actions(message, shop)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("manager.menu.shop_button", shop=shop.name),
                    callback_data=f"mgr:open:{shop.shop_id}",
                )
            ]
            for shop in shops
        ]
    )
    await message.answer(t("manager.menu.choose_shop"), reply_markup=keyboard)


@router.message(Command("handover"))
async def handle_handover(
    message: Message,
    run_service: RunService,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
    audit_repository: AuditRepository,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return
    args = (message.text or "").split()
    if len(args) < 4:
        await message.answer(HANDOVER_USAGE)
        return

    _, shop_id, role, username = args[:4]
    role = role.lower()
    username = username.lstrip("@")
    if role not in ("open", "close"):
        await message.answer(t("manager.errors.role_invalid"))
        return

    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        await message.answer(t("common.shop_not_found", shop_id=shop_id))
        return

    if not _is_manager(user.username, shop.manager_usernames):
        await message.answer(t("manager.errors.not_manager"))
        return

    target_user = await users_repository.get_by_username(username)
    if not target_user or not target_user.is_active:
        await message.answer(t("manager.errors.user_not_found", username=username))
        return
    if not (shop.allow_anyone or target_user.can_work_in_shop(shop_id)):
        await message.answer(
            t("manager.errors.user_not_in_shop", username=username, shop_id=shop_id)
        )
        return

    new_user = RunUser(
        user_id=target_user.tg_id,
        username=target_user.username,
        full_name=target_user.full_name,
    )
    try:
        run = await run_service.handover_role(shop_id, role, new_user)
    except RunNotFoundError:
        target = date.today().isoformat()
        await message.answer(t("manager.errors.run_missing", shop_id=shop_id, date=target))
        return

    await audit_repository.append(
        AuditRecord.create(
            action=f"handover_{role}",
            entity="run",
            entity_id=run.run_id,
            details=f"{role} → {target_user.username or target_user.full_name}",
            user_id=str(user.id),
        )
    )
    await message.answer(
        t(
            "manager.confirm.handover",
            role=_role_text(role),
            username=username,
            shop_id=shop_id,
        )
    )
    await _broadcast_shop_update(
        message.bot,
        shop_id,
        t(
            "manager.broadcast.handover",
            manager=_manager_name(user),
            role=_role_text(role),
            username=username,
            shop_id=shop_id,
        ),
        shops_repository,
        users_repository,
    )


@router.message(Command("create_run"))
async def handle_create_run(
    message: Message,
    run_service: RunService,
    shops_repository: ShopsRepository,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return
    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer(CREATE_USAGE)
        return
    _, shop_id, *rest = args
    run_date: str | None = None
    if rest:
        candidate = rest[0]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            await message.answer(t("manager.errors.invalid_date"))
            return
        run_date = candidate

    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        await message.answer(t("common.shop_not_found", shop_id=shop_id))
        return
    if not _is_manager(user.username, shop.manager_usernames):
        await message.answer(t("manager.errors.not_manager"))
        return

    try:
        run = await run_service.create_run(shop_id, run_date)
    except RunAlreadyExistsError:
        await message.answer(t("manager.errors.run_exists"))
        return

    display_date = run.date or (run_date or date.today().isoformat())
    await message.answer(t("manager.confirm.create", shop_id=shop_id, date=display_date))


@router.message(Command("return_run"))
async def handle_return_run(
    message: Message,
    run_service: RunService,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return
    args = (message.text or "").split()
    if len(args) < 3:
        await message.answer(RETURN_USAGE)
        return
    _, shop_id, *rest = args
    run_date: str | None = None
    reason_parts = rest
    if rest and _looks_like_date(rest[0]):
        run_date = rest[0]
        reason_parts = rest[1:]
    reason = " ".join(reason_parts).strip()
    if not reason:
        await message.answer(t("manager.errors.no_reason"))
        return
    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        await message.answer(t("common.shop_not_found", shop_id=shop_id))
        return
    if not _is_manager(user.username, shop.manager_usernames):
        await message.answer(t("manager.errors.not_manager"))
        return
    actor = RunUser(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
    try:
        run = await run_service.return_run(shop_id, actor, reason, run_date)
    except RunNotFoundError:
        target = run_date or date.today().isoformat()
        await message.answer(t("manager.errors.run_missing", shop_id=shop_id, date=target))
        return
    await message.answer(t("manager.confirm.return", shop_id=shop_id, date=run.date, reason=reason))
    await _broadcast_shop_update(
        message.bot,
        shop_id,
        t(
            "manager.broadcast.return",
            manager=_manager_name(user),
            shop_id=shop_id,
            reason=reason,
        ),
        shops_repository,
        users_repository,
    )
    manager_display = user.username or user.full_name or str(user.id)
    await _broadcast_shop_update(
        message.bot,
        shop_id,
        f"Менеджер @{manager_display} вернул смену {shop_id}: {reason}",
        shops_repository,
        users_repository,
    )


@router.callback_query(F.data.startswith("mgr:"))
async def handle_manager_callback(
    callback: CallbackQuery,
    run_service: RunService,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
) -> None:
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await callback.answer(t("common.invalid_command"), show_alert=True)
        return
    message_obj = _resolve_callback_message(callback.message)
    if message_obj is None:
        await callback.answer(t("common.invalid_command"), show_alert=True)
        return
    actor_user = callback.from_user
    if actor_user is None:
        await callback.answer(USER_REQUIRED_TEXT, show_alert=True)
        return
    _, action, shop_id, *rest = parts
    shop = await find_shop(shops_repository, shop_id)
    if not shop:
        await callback.answer(
            t("common.shop_not_found", shop_id=shop_id),
            show_alert=True,
        )
        return
    if not _is_manager(actor_user.username, shop.manager_usernames):
        await callback.answer(t("common.access_denied"), show_alert=True)
        return
    if action == "open":
        await _send_manager_actions(message_obj, shop)
        await callback.answer()
        return
    if action in {"handover_open", "handover_close"}:
        role = "open" if action.endswith("open") else "close"
        keyboard = await _build_employee_keyboard(shop, users_repository, role)
        if not keyboard:
            await callback.answer(
                t("manager.errors.handover_no_employees"),
                show_alert=True,
            )
            return
        await message_obj.answer(
            t(
                "manager.prompt.handover_select",
                role=_role_text(role),
                shop=shop.name,
            ),
            reply_markup=keyboard,
        )
        await callback.answer()
        return
    if action == "handover_user":
        if len(rest) < 2:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        role, username = rest[0], rest[1]
        record = await users_repository.get_by_username(username)
        if not record or not record.is_active:
            await callback.answer(
                t("manager.errors.user_not_found", username=username),
                show_alert=True,
            )
            return
        run_user = RunUser(
            user_id=record.tg_id,
            username=record.username,
            full_name=record.full_name,
        )
        try:
            await run_service.handover_role(shop_id, role, run_user)
        except RunNotFoundError:
            await callback.answer(
                t("manager.errors.run_not_started", shop_id=shop_id),
                show_alert=True,
            )
            return
        await message_obj.answer(
            t(
                "manager.confirm.handover",
                role=_role_text(role),
                username=username,
                shop_id=shop_id,
            )
        )
        await _broadcast_shop_update(
            message_obj.bot,
            shop_id,
            t(
                "manager.broadcast.handover",
                manager=_manager_name(actor_user),
                role=_role_text(role),
                username=username,
                shop_id=shop_id,
            ),
            shops_repository,
            users_repository,
        )
        await callback.answer(t("common.action_done"))
        return
    if action == "return":
        reason_code = rest[0] if rest else "no_z"
        reason = {
            "no_z": t("manager.return_reason.no_z"),
            "delta": t("manager.return_reason.delta"),
            "other": t("manager.return_reason.other"),
        }.get(reason_code, t("manager.return_reason.other"))
        actor = RunUser(
            user_id=actor_user.id,
            username=actor_user.username,
            full_name=actor_user.full_name,
        )
        try:
            run = await run_service.return_run(shop_id, actor, reason)
        except RunNotFoundError:
            target = date.today().isoformat()
            await callback.answer(
                t("manager.errors.run_missing", shop_id=shop_id, date=target),
                show_alert=True,
            )
            return
        await message_obj.answer(
            t(
                "manager.confirm.return",
                shop_id=shop_id,
                date=run.date,
                reason=reason,
            )
        )
        await _broadcast_shop_update(
            message_obj.bot,
            shop_id,
            t(
                "manager.broadcast.return",
                manager=_manager_name(actor_user),
                shop_id=shop_id,
                reason=reason,
            ),
            shops_repository,
            users_repository,
        )
        await callback.answer(t("common.action_done"))
        return
    if action == "exportday":
        await _export_single_day(
            message_obj,
            shop_id,
            runs_repository,
            runsteps_repository,
            attachments_repository,
            export_repository,
            shops_repository,
        )
        await callback.answer(t("common.action_done"))
        return
    if action == "exportweek":
        await _export_week(
            message_obj,
            shop_id,
            runs_repository,
            runsteps_repository,
            attachments_repository,
            export_repository,
            shops_repository,
        )
        await callback.answer(t("common.action_done"))
        return
    await callback.answer(t("manager.errors.unknown_action"), show_alert=True)


async def _find_shop(shops_repo: ShopsRepository, shop_id: str):
    shops = await shops_repo.list_active()
    for shop in shops:
        if shop.shop_id == shop_id:
            return shop
    return None


def _is_manager(requester_username: str | None, manager_usernames: list[str]) -> bool:
    if not requester_username:
        return False
    normalized = requester_username.lower().lstrip("@")
    manager_set = {name.lower().lstrip("@") for name in manager_usernames}
    return normalized in manager_set


def _looks_like_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


async def _manager_shops(username: str | None, shops_repo: ShopsRepository):
    if not username:
        return []
    shops = await shops_repo.list_active()
    return [shop for shop in shops if _is_manager(username, shop.manager_usernames)]


async def _send_manager_actions(
    message: Message,
    shop,
) -> None:
    await message.answer(
        t("manager.menu.actions_title", shop=shop.name, shop_id=shop.shop_id),
        reply_markup=_build_manager_actions(shop.shop_id),
    )


def _build_manager_actions(shop_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("manager.buttons.handover_open"),
                    callback_data=f"mgr:handover_open:{shop_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("manager.buttons.handover_close"),
                    callback_data=f"mgr:handover_close:{shop_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("manager.buttons.return_no_z"),
                    callback_data=f"mgr:return:{shop_id}:no_z",
                ),
                InlineKeyboardButton(
                    text=t("manager.buttons.return_delta"),
                    callback_data=f"mgr:return:{shop_id}:delta",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("manager.buttons.export_day"),
                    callback_data=f"mgr:exportday:{shop_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("manager.buttons.export_week"),
                    callback_data=f"mgr:exportweek:{shop_id}",
                )
            ],
        ]
    )


async def _build_employee_keyboard(
    shop,
    users_repository: UsersRepository,
    role: str,
) -> InlineKeyboardMarkup | None:
    usernames = shop.employee_usernames
    buttons = []
    for username in usernames:
        record = await users_repository.get_by_username(username)
        if not record:
            continue
        buttons.append(
            InlineKeyboardButton(
                text=f"@{username}",
                callback_data=f"mgr:handover_user:{shop.shop_id}:{role}:{username}",
            )
        )
    if not buttons:
        return None
    rows = [[btn] for btn in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _broadcast_shop_update(
    bot,
    shop_id: str,
    text: str,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    if bot is None:
        return
    dispatcher = getattr(bot, "dispatcher", None)
    manager_ids: list[int] = []
    if dispatcher and hasattr(dispatcher, "get"):
        manager_ids = dispatcher.get("manager_notify_chat_ids", [])
    shop_ids = await collect_shop_chat_ids(shop_id, shops_repository, users_repository)
    await broadcast_to_targets(bot, text, manager_ids, shop_ids)


def _manager_name(user) -> str:
    return user.username or user.full_name or str(user.id)


async def _export_single_day(
    message: Message,
    shop_id: str,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository,
) -> None:
    target = date.today().isoformat()
    run = await runs_repository.get_run(shop_id, target)
    if not run:
        await message.answer(t("manager.errors.run_missing", shop_id=shop_id, date=target))
        return
    record, total_delta = await append_export_record(
        run,
        runsteps_repository,
        attachments_repository,
        export_repository,
        shops_repository=shops_repository,
    )
    record.period_start = record.period_end = target
    await message.answer(
        t(
            "manager.confirm.export_day",
            shop_id=shop_id,
            date=target,
            delta=f"{total_delta:+.2f}",
        )
    )


async def _export_week(
    message: Message,
    shop_id: str,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository,
) -> None:
    runs = await runs_repository.list_runs()
    reference = date.today()
    start = reference - timedelta(days=6)
    exported = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        run = next(
            (r for r in runs if r.shop_id == shop_id and r.date == day.isoformat()),
            None,
        )
        if not run:
            continue
        record, total_delta = await append_export_record(
            run,
            runsteps_repository,
            attachments_repository,
            export_repository,
            shops_repository=shops_repository,
        )
        record.period_start = record.period_end = day.isoformat()
        exported.append((day.isoformat(), total_delta))
    if not exported:
        await message.answer(t("status.export_none"))
        return
    details = "\n".join(f"- {day}: Δ={delta:+.2f}" for day, delta in exported)
    period = f"{start.isoformat()} – {reference.isoformat()}"
    await message.answer(
        t(
            "manager.confirm.export_week",
            shop_id=shop_id,
            period=period,
            details=details,
        )
    )
