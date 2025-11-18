from __future__ import annotations

import json
from typing import Any, TypedDict

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.types import (
    User as TelegramUser,
)

from retailcheck.attachments.models import AttachmentRecord
from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.models import AuditRecord
from retailcheck.audit.repository import AuditRepository
from retailcheck.bot.states.step_flow import StepFlowState
from retailcheck.bot.utils.access import ensure_user_allowed
from retailcheck.localization import gettext as t
from retailcheck.runs.models import RunRecord
from retailcheck.runs.service import RunService, RunUser
from retailcheck.runsteps.models import RunStepRecord
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository

router = Router()
USER_REQUIRED_TEXT = "Не удалось определить пользователя. Попробуйте снова."


class SerializedStep(TypedDict):
    code: str
    title: str
    type: str
    hint: str
    required: bool
    validators: dict[str, Any]
    owner_role: str


def _resolve_callback_message(message: Message | InaccessibleMessage | None) -> Message | None:
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return message


def _render_step_prompt(step: SerializedStep, index: int, total: int) -> str:
    hint = step["hint"] or ""
    required = "обязательный" if step.get("required") else "необязательный"
    return (
        f"Шаг {index + 1}/{total}\n"
        f"<b>{step['title']}</b> ({required}, тип {step['type']})\n"
        f"{hint}\n\n"
        "Если хотите вернуться или пропустить (для необязательного шага), используйте кнопки ниже."
    )


def _resolve_template_id(run: RunRecord, phase: str) -> str:
    return run.get_template_for_phase(phase)


@router.message(Command("open"))
async def start_open_steps(
    message: Message,
    run_service: RunService,
    template_repository: TemplateRepository,
    state: FSMContext,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    await _start_steps_flow(
        message,
        run_service,
        template_repository,
        state,
        shops_repository,
        users_repository,
        phase="open",
        shop_override=None,
    )


@router.message(Command("close"))
async def start_close_steps(
    message: Message,
    run_service: RunService,
    template_repository: TemplateRepository,
    state: FSMContext,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    await _start_steps_flow(
        message,
        run_service,
        template_repository,
        state,
        shops_repository,
        users_repository,
        phase="close",
        shop_override=None,
    )


@router.callback_query(F.data.startswith("start:"))
async def handle_start_callback(
    callback: CallbackQuery,
    run_service: RunService,
    template_repository: TemplateRepository,
    state: FSMContext,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
) -> None:
    data = callback.data or ""
    message_obj = _resolve_callback_message(callback.message)
    if message_obj is None:
        await callback.answer(t("common.invalid_command"), show_alert=True)
        return
    _, role, shop_id = data.split(":", 2)
    await _start_steps_flow(
        message_obj,
        run_service,
        template_repository,
        state,
        shops_repository,
        users_repository,
        phase=role,
        shop_override=shop_id,
        actor=callback.from_user,
    )
    await callback.answer()


async def _start_steps_flow(
    message: Message,
    run_service: RunService,
    template_repository: TemplateRepository,
    state: FSMContext,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
    phase: str,
    shop_override: str | None,
    actor: TelegramUser | None = None,
) -> None:
    user = actor or message.from_user
    if user is None:
        await message.answer(USER_REQUIRED_TEXT)
        return
    run_user = RunUser(user_id=user.id, username=user.username, full_name=user.full_name)
    shop_id = shop_override or _extract_shop_id(message.text) or "shop_1"
    try:
        await ensure_user_allowed(user, shop_id, shops_repository, users_repository)
    except PermissionError:
        await message.answer("У вас нет доступа к этому магазину.")
        return
    except ValueError:
        await message.answer("Магазин не найден.")
        return
    try:
        role = "open" if phase == "open" else "close"
        result = await run_service.assign_role(shop_id, role, run_user)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось подготовить шаги: {exc}")
        return

    template_id = _resolve_template_id(result.run, phase)
    try:
        template = template_repository.get(template_id)
    except KeyError:
        await message.answer("Не найден шаблон шагов. Свяжитесь с администратором.")
        return

    owner_filter = "opener" if result.role == "open" else "closer"
    serialized_steps: list[SerializedStep] = [
        _serialize_step(step)
        for step in template.steps
        if getattr(step, "owner_role", "shared") in ("shared", owner_filter)
    ]
    if not serialized_steps:
        await message.answer(t("start.no_steps_for_role"))
        return

    await state.set_state(StepFlowState.waiting_input)
    await state.update_data(
        {
            "run_id": result.run.run_id,
            "phase": phase,
            "owner_role": owner_filter,
            "step_index": 0,
            "steps": serialized_steps,
        }
    )
    await message.answer(
        _render_step_prompt(serialized_steps[0], 0, len(serialized_steps)),
        reply_markup=_build_step_keyboard(serialized_steps[0]),
    )


@router.message(StepFlowState.waiting_input)
async def handle_step_input(
    message: Message,
    state: FSMContext,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    audit_repository: AuditRepository,
) -> None:
    data = await state.get_data()
    current_idx = data.get("step_index", 0)
    steps = data.get("steps", [])
    if not steps:
        await message.answer("Не удалось загрузить шаги. Попробуйте перезапустить сценарий.")
        await state.clear()
        return

    current_step = steps[current_idx]
    awaiting_comment = data.get("pending_comment")
    if awaiting_comment:
        comment_text = (message.text or "").strip()
        if not comment_text:
            await message.answer("Комментарий не может быть пустым.")
            return
        pending_record = RunStepRecord(**awaiting_comment)
        pending_record.comment = comment_text
        pending_record.status = "ok"
        await runsteps_repository.upsert([pending_record])
        await _log_step_update(audit_repository, message, pending_record)
        await state.update_data(pending_comment=None)
        message_to_user = "Комментарий сохранён. Продолжаем шаги."
        await message.answer(message_to_user)
        # move to next step without reprocessing current value
        next_idx = data.get("step_index", 0) + 1
        steps = data.get("steps", [])
        if next_idx >= len(steps):
            await message.answer(
                "Все шаги пройдены. Итоговая логика появится позже.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await state.clear()
            return
        await state.update_data(step_index=next_idx)
        await message.answer(_render_step_prompt(steps[next_idx], next_idx, len(steps)))
        return

    user_input = (message.text or "").strip()
    if user_input.casefold() in {"/back", t("steps.button.back").strip().casefold()}:
        if current_idx == 0:
            await message.answer("Вы на первом шаге, возврат невозможен.")
            return
        prev_idx = current_idx - 1
        await state.update_data(step_index=prev_idx)
        await message.answer(_render_step_prompt(steps[prev_idx], prev_idx, len(steps)))
        return
    if user_input.casefold() in {"/skip", t("steps.button.skip").strip().casefold()}:
        if current_step.get("required"):
            await message.answer("Нельзя пропустить обязательный шаг.")
            return
        record = RunStepRecord(
            run_id=data.get("run_id", ""),
            phase=data.get("phase", "open"),
            step_code=current_step["code"],
            owner_role=current_step.get("owner_role") or "shared",
            performer_user_id=str(message.from_user.id) if message.from_user else None,
            status="skipped",
            comment="Skipped by user",
        )
        await runsteps_repository.upsert([record])
        await _log_step_update(audit_repository, message, record)
    else:
        try:
            record, attachments, comment_required = _build_record_from_message(
                message,
                current_step,
                data.get("run_id", ""),
                data.get("phase", "open"),
                str(message.from_user.id) if message.from_user else None,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await runsteps_repository.upsert([record])
        await _log_step_update(audit_repository, message, record)
        for attachment in attachments:
            await attachments_repository.add(attachment)
        if comment_required:
            await state.update_data(pending_comment=record.__dict__)
            await message.answer(
                "Δ превышает порог. Пожалуйста, введите комментарий для объяснения расхождения."
            )
            return

    if current_idx + 1 >= len(steps):
        await message.answer(
            "Все шаги пройдены. Итоговая логика появится позже.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return
    next_idx = current_idx + 1
    await state.update_data(step_index=next_idx)
    await message.answer(
        _render_step_prompt(steps[next_idx], next_idx, len(steps)),
        reply_markup=_build_step_keyboard(steps[next_idx]),
    )


def _extract_shop_id(command_text: str | None) -> str | None:
    if not command_text:
        return None
    parts = command_text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


def _serialize_step(step) -> SerializedStep:
    return {
        "code": step.code,
        "title": step.title,
        "type": step.type,
        "hint": step.hint or "",
        "required": step.required,
        "validators": _load_validators(step.validators_json),
        "owner_role": getattr(step, "owner_role", "shared"),
    }


def _load_validators(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


async def _log_step_update(
    audit_repository: AuditRepository,
    message: Message,
    record: RunStepRecord,
) -> None:
    if not audit_repository:
        return
    value = record.value_number or record.value_text or record.value_check or ""
    details = f"{record.phase}:{record.step_code} status={record.status} value={value}"
    if record.comment:
        details += f" comment={record.comment}"
    audit_record = AuditRecord.create(
        action="step_update",
        entity="run_step",
        entity_id=f"{record.run_id}:{record.step_code}",
        details=details,
        user_id=str(message.from_user.id) if message.from_user else None,
    )
    await audit_repository.append(audit_record)


def _build_record_from_message(
    message: Message,
    step: SerializedStep,
    run_id: str,
    phase: str,
    performer_user_id: str | None,
) -> tuple[RunStepRecord, list[AttachmentRecord], bool]:
    step_type = step["type"]
    owner_role = step.get("owner_role") or "shared"
    attachments: list[AttachmentRecord] = []
    comment_required = False
    if step_type == "number":
        value, comment_required, delta_value = _parse_number_value(message.text, step["validators"])
        record = RunStepRecord(
            run_id=run_id,
            phase=phase,
            step_code=step["code"],
            owner_role=owner_role,
            value_number=value,
            delta_number=delta_value,
            performer_user_id=performer_user_id,
            status="ok",
        )
    elif step_type == "text":
        if not message.text:
            raise ValueError("Введите текстовое значение.")
        text_value = message.text.strip()
        if not text_value and step.get("required"):
            raise ValueError("Шаг обязательный, текст не может быть пустым.")
        record = RunStepRecord(
            run_id=run_id,
            phase=phase,
            step_code=step["code"],
            owner_role=owner_role,
            value_text=text_value,
            performer_user_id=performer_user_id,
            status="ok",
        )
    elif step_type == "check":
        if not message.text:
            raise ValueError("Напишите 'да' или 'нет'.")
        bool_value = _parse_bool_value(message.text)
        record = RunStepRecord(
            run_id=run_id,
            phase=phase,
            step_code=step["code"],
            owner_role=owner_role,
            value_check="TRUE" if bool_value else "FALSE",
            performer_user_id=performer_user_id,
            status="ok",
        )
    elif step_type == "photo":
        file_id = _extract_photo_file_id(message)
        if not file_id:
            raise ValueError("Отправьте фото (как изображение или документ).")
        attachments.append(
            AttachmentRecord(
                run_id=run_id,
                step_code=step["code"],
                telegram_file_id=file_id,
                kind=step["code"],
            )
        )
        comment = (message.caption or "").strip()
        record = RunStepRecord(
            run_id=run_id,
            phase=phase,
            step_code=step["code"],
            owner_role=owner_role,
            value_text=f"photo:{file_id}",
            comment=comment or None,
            performer_user_id=performer_user_id,
            status="ok",
        )
    else:
        raise ValueError(f"Тип шага '{step_type}' пока не поддержан.")
    return record, attachments, comment_required


def _parse_number_value(
    text: str | None,
    validators: dict[str, Any],
) -> tuple[str, bool, str | None]:
    if not text:
        raise ValueError("Введите число.")
    cleaned = text.replace(",", ".").strip()
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError("Неверный формат числа, используйте точку или запятую.") from exc
    min_value = validators.get("min")
    max_value = validators.get("max")
    if min_value is not None and value < float(min_value):
        raise ValueError(f"Значение должно быть ≥ {min_value}.")
    if max_value is not None and value > float(max_value):
        raise ValueError(f"Значение должно быть ≤ {max_value}.")
    delta_value = None
    comment_required = False
    if "norm" in validators:
        delta_value = f"{value - float(validators['norm']):.2f}"
        threshold = float(validators.get("delta_threshold", 0))
        if threshold and abs(value - float(validators["norm"])) >= threshold:
            comment_required = True
    return cleaned, comment_required, delta_value


def _parse_bool_value(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized in {"1", "true", "да", "yes", "y", "ok", "👍"}:
        return True
    if normalized in {"0", "false", "нет", "no", "n"}:
        return False
    raise ValueError("Ответьте 'да' или 'нет'.")


def _extract_photo_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return message.document.file_id
    return None


def _build_step_keyboard(step: SerializedStep) -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text=t("steps.button.back"))]]
    if not step.get("required"):
        buttons.append([KeyboardButton(text=t("steps.button.skip"))])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=False)
