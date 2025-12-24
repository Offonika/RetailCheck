from __future__ import annotations

from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.types import (
    User as TelegramUser,
)

from retailcheck.bot.handlers.steps import _start_steps_flow
from retailcheck.bot.utils.access import ensure_user_allowed, find_shop, user_can_access_shop
from retailcheck.localization import gettext as t
from retailcheck.runs.service import (
    RoleAlreadyTakenError,
    RoleAssignmentResult,
    RunNotFoundError,
    RunService,
    RunUser,
)
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository

router = Router()
MAX_SHOPS_PER_PAGE = 6
USER_REQUIRED_TEXT = "Не удалось определить пользователя. Выполните команду из личного чата."


def _resolve_callback_message(message: Message | InaccessibleMessage | None) -> Message | None:
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return message


async def _remove_actions_keyboard(message: Message | InaccessibleMessage | None) -> None:
    message_obj = _resolve_callback_message(message)
    if message_obj is None:
        return
    try:
        await message_obj.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def _require_user(message: Message) -> TelegramUser | None:
    user = message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return None
    return user


def parse_payload(payload: str | None) -> tuple[str, str] | None:
    if not payload:
        return None
    if "__" not in payload:
        return None
    shop_part, role = payload.split("__", 1)
    if not shop_part.startswith("shop_"):
        return None
    shop_id = shop_part
    if role not in ("open", "close"):
        return None
    return shop_id, role


def format_role(role: str) -> str:
    return t(f"roles.{role}") if role in {"open", "close"} else role


def format_username(username: str | None) -> str:
    return f"@{username}" if username else t("start.someone_else")


@router.message(CommandStart())
async def handle_start(
    message: Message,
    command: CommandObject,
    run_service: RunService,
    shops_repository: ShopsRepository | None = None,
    users_repository: UsersRepository | None = None,
) -> None:
    payload = parse_payload(command.args)
    if not payload:
        if not shops_repository:
            await message.answer(t("start.no_shops"))
            return
        await _show_shop_list(
            message,
            page=0,
            shops_repository=shops_repository,
            users_repository=users_repository,
            edit=False,
        )
        return

    shop_id, role = payload
    user = await _require_user(message)
    if user is None:
        return
    try:
        await ensure_user_allowed(user, shop_id, shops_repository, users_repository)
    except PermissionError:
        await message.answer(t("start.no_access"))
        return
    except ValueError:
        await message.answer(t("start.shop_not_found", shop_id=shop_id))
        return
    run_user = RunUser(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    try:
        result = await run_service.assign_role(shop_id, role, run_user)
    except RoleAlreadyTakenError as exc:
        await message.answer(
            t(
                "start.role_taken",
                role=format_role(role).capitalize(),
                username=format_username(exc.username),
            )
        )
        return
    except RunNotFoundError:
        # For closer role, run must exist (created by opener first)
        # For open role, this shouldn't happen as opener creates the run
        if role == "close":
            await message.answer(
                t("start.run_missing") + " "
                "Сначала должен быть открыт run (назначен opener)."
            )
        else:
            await message.answer(t("start.run_missing"))
        return
    except ValueError:
        await message.answer(t("start.invalid_link"))
        return

    await message.answer(build_success_message(result, shop_id))


@router.callback_query(F.data.startswith("start:"))
async def handle_start_callback(
    callback: CallbackQuery,
    run_service: RunService,
    template_repository: TemplateRepository,
    state: FSMContext,
    shops_repository: ShopsRepository | None = None,
    users_repository: UsersRepository | None = None,
) -> None:
    data = callback.data or ""
    if not data.startswith("start:"):
        return
    parts = data.split(":")
    if len(parts) < 2:
        await callback.answer(t("common.invalid_command"), show_alert=True)
        return
    _, action, *rest = parts
    if action == "select":
        if not shops_repository or len(rest) < 1:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        message_obj = _resolve_callback_message(callback.message)
        if message_obj is None:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        shop_id = rest[0]
        page = int(rest[1]) if len(rest) > 1 else 0
        await _show_shop_actions(
            message_obj,
            shop_id,
            page,
            shops_repository,
            edit=True,
        )
        await callback.answer()
        return
    if action == "list":
        if not shops_repository:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        message_obj = _resolve_callback_message(callback.message)
        if message_obj is None:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        page = int(rest[0]) if rest else 0
        await _show_shop_list(
            message_obj,
            page=page,
            shops_repository=shops_repository,
            users_repository=users_repository,
            edit=True,
        )
        await callback.answer()
        return
    if action == "menu":
        message_obj = _resolve_callback_message(callback.message)
        if message_obj is None:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        await message_obj.answer(t("start.menu_prompt"))
        await callback.answer()
        return
    if action in {"open", "close", "continue"}:
        if not rest:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        if not shops_repository or not users_repository:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        message_obj = _resolve_callback_message(callback.message)
        if message_obj is None:
            await callback.answer(t("common.invalid_command"), show_alert=True)
            return
        shop_id = rest[0]
        # Map action to phase: open→open, continue→continue, close→close
        phase_mapping = {"open": "open", "continue": "continue", "close": "close"}
        phase = phase_mapping[action]
        await _start_steps_flow(
            message_obj,
            run_service,
            template_repository,
            state,
            shops_repository,
            users_repository,
            phase=phase,
            shop_override=shop_id,
            actor=callback.from_user,
        )
        await _remove_actions_keyboard(callback.message)
        await callback.answer()
        return
    await callback.answer(t("common.invalid_command"), show_alert=True)


def build_success_message(result: RoleAssignmentResult, shop_id: str) -> str:
    if result.state == "already_holder":
        return t("start.already_holder", role=format_role(result.role), shop=shop_id)
    if result.role == "open":
        return t("start.assign_open_success", shop_id=shop_id)
    return t("start.assign_close_success", shop_id=shop_id)


@router.message(Command("menu"))
async def handle_menu(
    message: Message,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    user = await _require_user(message)
    if user is None:
        return
    keyboard = await _build_command_keyboard(message, shops_repository, users_repository)
    if not keyboard:
        await message.answer(t("start.menu_none"))
        return
    await message.answer(t("start.menu_title"), reply_markup=keyboard)


@router.message(Command("whoami"))
async def handle_whoami(message: Message) -> None:
    user = await _require_user(message)
    if user is None:
        return
    await message.answer(
        "\n".join(
            [
                f"tg_id: {user.id}",
                f"username: @{user.username or '—'}",
                f"имя: {user.full_name}",
            ]
        )
    )


@router.message(Command("hide"))
async def handle_hide(message: Message) -> None:
    await message.answer(
        t("start.menu_hidden"),
        reply_markup=ReplyKeyboardRemove(),
    )


async def _build_command_keyboard(
    message: Message,
    shops_repository: ShopsRepository | None,
    users_repository: UsersRepository | None,
) -> ReplyKeyboardMarkup | None:
    if not shops_repository or not users_repository:
        return None
    user = message.from_user
    if user is None:
        return None
    shops = await shops_repository.list_active()
    rows: list[list[KeyboardButton]] = []
    for shop in shops:
        allowed = await user_can_access_shop(
            user,
            shop.shop_id,
            shops_repository,
            users_repository,
        )
        if not allowed:
            continue
        rows.append(
            [
                KeyboardButton(text=t("menu.row_status", shop_id=shop.shop_id)),
                KeyboardButton(text=t("menu.row_summary", shop_id=shop.shop_id)),
            ]
        )
        rows.append(
            [
                KeyboardButton(text=t("menu.row_export", shop_id=shop.shop_id)),
                KeyboardButton(text=t("menu.row_export_week", shop_id=shop.shop_id)),
            ]
        )
    if not rows:
        return None
    rows.append([KeyboardButton(text=t("start.menu_hide"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def _show_shop_list(
    message: Message,
    page: int,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository | None,
    *,
    edit: bool,
) -> None:
    user = message.from_user
    if user is None:
        warning = USER_REQUIRED_TEXT
        if edit:
            await message.edit_text(warning)
        else:
            await message.answer(warning)
        return
    shops = await _list_accessible_shops(user, shops_repository, users_repository)
    if not shops:
        text = t("start.no_shops")
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    total_pages = max(1, ceil(len(shops) / MAX_SHOPS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    keyboard = _build_shop_list_keyboard(shops, page, total_pages)
    text = t("start.pick_shop")
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


async def _show_shop_actions(
    message: Message,
    shop_id: str,
    page: int,
    shops_repository: ShopsRepository,
    *,
    edit: bool,
) -> None:
    shop = await find_shop(shops_repository, shop_id)
    shop_name = shop.name if shop else shop_id
    dual_mode = bool(shop.dual_cash_mode) if shop else False
    keyboard = _build_shop_actions_keyboard(shop_id, page, dual_mode=dual_mode)
    text = t("start.choose_action", shop=shop_name)
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


def _build_shop_list_keyboard(
    shops,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    start = page * MAX_SHOPS_PER_PAGE
    chunk = shops[start : start + MAX_SHOPS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=shop.name,
                callback_data=f"start:select:{shop.shop_id}:{page}",
            )
        ]
        for shop in chunk
    ]
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text=t("start.button.prev"),
                    callback_data=f"start:list:{page - 1}",
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text=t("start.button.next"),
                    callback_data=f"start:list:{page + 1}",
                )
            )
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton(text=t("start.button.more"), callback_data="start:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_shop_actions_keyboard(
    shop_id: str,
    page: int,
    *,
    dual_mode: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("start.button.open"),
                callback_data=f"start:open:{shop_id}",
            )
        ]
    ]
    if dual_mode:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("start.button.continue"),
                    callback_data=f"start:continue:{shop_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("start.button.close"),
                callback_data=f"start:close:{shop_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("start.button.back"),
                callback_data=f"start:list:{page}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text=t("start.button.more"), callback_data="start:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _list_accessible_shops(
    user: TelegramUser,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository | None,
) -> list:
    shops = await shops_repository.list_active()
    if not users_repository:
        return shops
    accessible = []
    for shop in shops:
        try:
            allowed = await user_can_access_shop(
                user,
                shop.shop_id,
                shops_repository,
                users_repository,
            )
        except Exception:
            allowed = False
        if allowed:
            accessible.append(shop)
    return accessible
