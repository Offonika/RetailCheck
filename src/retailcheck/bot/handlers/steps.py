from __future__ import annotations

import json
from typing import Any, TypedDict

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.types import User as TelegramUser
from loguru import logger

from retailcheck.attachments.models import AttachmentRecord
from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.models import AuditRecord
from retailcheck.audit.repository import AuditRepository
from retailcheck.bot.states.step_flow import StepFlowState
from retailcheck.bot.utils.access import ensure_user_allowed
from retailcheck.localization import gettext as t
from retailcheck.runs.models import RunRecord
from retailcheck.runs.service import RunService, RunUser
from retailcheck.runsteps.models import RunStepRecord, now_iso
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository

router = Router()
USER_REQUIRED_TEXT = "Не удалось определить пользователя. Попробуйте снова."
TERMINAL_CHOICES = {
    "tbank": "T-Bank",
    "t-bank": "T-Bank",
    "tb": "T-Bank",
    "тбанк": "T-Bank",
    "т-ббанк": "T-Bank",
    "т-банк": "T-Bank",
    "тб": "T-Bank",
    "sberbank": "Sberbank",
    "sber": "Sberbank",
    "сбербанк": "Sberbank",
    "сбер": "Sberbank",
    "third": "Третий терминал",
    "3": "Третий терминал",
    "третьийтерминал": "Третий терминал",
    "3terminal": "Третий терминал",
    "thirdterminal": "Третий терминал",
    "третьий": "Третий терминал",
    "другой": "Третий терминал",
}


class SerializedStep(TypedDict):
    code: str
    title: str
    type: str
    hint: str
    required: bool
    validators: dict[str, Any]
    owner_role: str


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
    current_state = await state.get_state()
    if current_state == StepFlowState.preparing.state:
        logger.info(
            "Step flow already initializing; ignoring duplicate CTA (user=%s, shop=%s, phase=%s)",
            user.id,
            shop_id,
            phase,
        )
        await message.answer(t("steps.starting"))
        return
    if current_state == StepFlowState.waiting_input.state:
        logger.info(
            "Step flow already active; ignoring duplicate CTA (user=%s, shop=%s, phase=%s)",
            user.id,
            shop_id,
            phase,
        )
        await message.answer(t("steps.already_running"))
        return
    state_locked = False
    try:
        await state.set_state(StepFlowState.preparing)
        state_locked = True
        # Map phase to role:
        # - "open" → role "open" (A starts the run)
        # - "continue" → role "close" (B joins existing run for daytime steps)
        # - "close" → role "close" (B finalizes the run)
        role = "open" if phase == "open" else "close"
        result = await run_service.assign_role(shop_id, role, run_user)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось подготовить шаги: {exc}")
        return
    finally:
        # Always clear state if we set it but didn't complete initialization
        if state_locked:
            current_state = await state.get_state()
            if current_state == StepFlowState.preparing.state:
                await state.clear()

    template_id = _resolve_template_id(result.run, phase)
    try:
        template = template_repository.get(template_id)
    except KeyError:
        await state.clear()
        await message.answer("Не найден шаблон шагов. Свяжитесь с администратором.")
        return

    # Determine owner_filter based on role (A=opener, B=closer)
    owner_filter = "opener" if result.role == "open" else "closer"
    # Filter steps: include steps for this role, shared steps, and "both" steps
    allowed_roles = {"shared", owner_filter, "both"}
    serialized_steps: list[SerializedStep] = [
        _serialize_step(step)
        for step in template.steps
        if getattr(step, "owner_role", "shared").lower() in allowed_roles
    ]
    if not serialized_steps:
        await message.answer(t("start.no_steps_for_role"))
        await state.clear()
        return

    await state.set_state(StepFlowState.waiting_input)
    await state.update_data(
        {
            "run_id": result.run.run_id,
            "shop_id": shop_id,
            "phase": phase,
            "owner_role": owner_filter,
            "step_index": 0,
            "steps": serialized_steps,
            "completed_steps": [],
            "input_lock_step": None,
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
    actor_role = data.get("owner_role") or "shared"
    effective_owner_role = _effective_owner_role(current_step, actor_role)
    # Terminal steps: selection + photo (new) and legacy formats
    step_code = current_step.get("code", "")
    is_terminal_choice = step_code == "terminal_choice"
    is_terminal_step = step_code in {
        "photo_terminal",
        "photo_terminal_1",
        "photo_terminal_sber",
        "photo_terminal_tbank",
    }
    is_specific_terminal = step_code in {"photo_terminal_sber", "photo_terminal_tbank"}
    terminal_type = data.get("terminal_type")
    selected_terminal = data.get("selected_terminal") or terminal_type
    completed_steps: list[int] = data.get("completed_steps") or []
    input_lock_step = data.get("input_lock_step")
    awaiting_comment = data.get("pending_comment")
    if awaiting_comment:
        comment_text = (message.text or "").strip()
        if not comment_text:
            await message.answer("Комментарий не может быть пустым.")
            return
        # Check and set lock atomically to prevent race conditions
        if input_lock_step == current_idx:
            await message.answer(t("steps.processing"))
            return
        # Set lock before processing
        await state.update_data(input_lock_step=current_idx)
        # Re-check after setting lock to ensure we're still the only one processing
        updated_data = await state.get_data()
        if updated_data.get("input_lock_step") != current_idx:
            await message.answer(t("steps.processing"))
            return
        pending_record = RunStepRecord(**awaiting_comment)
        pending_record.comment = comment_text
        pending_record.status = "ok"
        pending_record.updated_at = now_iso()
        try:
            await runsteps_repository.upsert([pending_record])
            await _log_step_update(audit_repository, message, pending_record)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save comment for step %s: %s", pending_record.step_code, exc)
            await state.update_data(input_lock_step=None)
            await message.answer("Не удалось сохранить шаг, попробуйте ещё раз.")
            return
        await state.update_data(pending_comment=None, input_lock_step=None)
        message_to_user = "Комментарий сохранён. Продолжаем шаги."
        await message.answer(message_to_user)
        # move to next step without reprocessing current value
        next_idx = data.get("step_index", 0) + 1
        steps = data.get("steps", [])
        if next_idx >= len(steps):
            await message.answer(
                _final_prompt(data.get("shop_id")),
                reply_markup=ReplyKeyboardRemove(),
            )
            await state.clear()
            return
        completed_steps.append(current_idx)
        await state.update_data(
            step_index=next_idx,
            completed_steps=completed_steps,
        )
        await message.answer(_render_step_prompt(steps[next_idx], next_idx, len(steps)))
        return

    user_input = (message.text or "").strip()
    if user_input.casefold() in {"/back", t("steps.button.back").strip().casefold()}:
        if current_idx == 0:
            await message.answer("Вы на первом шаге, возврат невозможен.")
            return
        prev_idx = current_idx - 1
        prompt = _render_step_prompt(steps[prev_idx], prev_idx, len(steps))
        await message.answer(
            f"{prompt}\n\n{t('steps.back_readonly')}"
        )
        await message.answer(_render_step_prompt(current_step, current_idx, len(steps)))
        return
    if user_input.casefold() in {"/skip", t("steps.button.skip").strip().casefold()}:
        if current_step.get("required"):
            await message.answer("Нельзя пропустить обязательный шаг.")
            return
        if input_lock_step == current_idx:
            await message.answer(t("steps.processing"))
            return
        await state.update_data(input_lock_step=current_idx)
        record = RunStepRecord(
            run_id=data.get("run_id", ""),
            phase=data.get("phase", "open"),
            step_code=current_step["code"],
            owner_role=effective_owner_role,
            performer_user_id=str(message.from_user.id) if message.from_user else None,
            status="skipped",
            comment="Skipped by user",
        )
        try:
            await runsteps_repository.upsert([record])
            await _log_step_update(audit_repository, message, record)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save skipped step %s: %s", record.step_code, exc)
            await state.update_data(input_lock_step=None)
            await message.answer("Не удалось сохранить шаг, попробуйте ещё раз.")
            return
    else:
        if input_lock_step == current_idx:
            await message.answer(t("steps.processing"))
            return
        has_photo = bool(_extract_photo_file_id(message))
        if is_terminal_choice:
            normalized_choice = _normalize_terminal_choice(message.text)
            if not normalized_choice:
                await message.answer(
                    t("steps.terminal.choose_prompt"),
                    reply_markup=_build_step_keyboard(current_step),
                )
                await state.update_data(input_lock_step=None)
                return
            await state.update_data(input_lock_step=current_idx)
            record = RunStepRecord(
                run_id=data.get("run_id", ""),
                phase=data.get("phase", "open"),
                step_code=current_step["code"],
                owner_role=effective_owner_role,
                value_text=normalized_choice,
                performer_user_id=str(message.from_user.id) if message.from_user else None,
                status="ok",
            )
            await runsteps_repository.upsert([record])
            await _log_step_update(audit_repository, message, record)
            await state.update_data(
                selected_terminal=normalized_choice,
                terminal_type=normalized_choice,
                input_lock_step=None,
            )
        else:
            if is_specific_terminal and not has_photo:
                await message.answer(
                    f"Загрузите фото сверки терминала ({current_step.get('title', '')}).",
                    reply_markup=_build_step_keyboard(current_step),
                )
                await state.update_data(input_lock_step=None)
                return
            if is_terminal_step and not is_specific_terminal:
                normalized_choice = _normalize_terminal_choice(message.text)
                if normalized_choice and not has_photo:
                    await state.update_data(
                        selected_terminal=normalized_choice,
                        terminal_type=normalized_choice,
                        input_lock_step=None,
                    )
                    await message.answer(
                        t("steps.terminal.chosen").format(terminal=normalized_choice),
                        reply_markup=_build_step_keyboard(current_step),
                    )
                    return
                terminal_choice_value = selected_terminal or normalized_choice
                if not terminal_choice_value:
                    await message.answer(
                        t("steps.terminal.choose_prompt"),
                        reply_markup=_build_step_keyboard(current_step),
                    )
                    await state.update_data(input_lock_step=None)
                    return
                if not has_photo:
                    await message.answer(
                        t("steps.terminal.need_photo"),
                        reply_markup=_build_step_keyboard(current_step),
                    )
                    await state.update_data(input_lock_step=None)
                    return
            await state.update_data(input_lock_step=current_idx)
            # Determine terminal type for attachment kind
            effective_terminal = None
            if is_specific_terminal:
                effective_terminal = "Sberbank" if "sber" in step_code else "T-Bank"
            elif is_terminal_step:
                effective_terminal = selected_terminal or _normalize_terminal_choice(message.text)
            try:
                record, attachments, comment_required = _build_record_from_message(
                    message,
                    current_step,
                    data.get("run_id", ""),
                    data.get("phase", "open"),
                    str(message.from_user.id) if message.from_user else None,
                    owner_role=effective_owner_role,
                    terminal_type=effective_terminal,
                )
            except ValueError as exc:
                await message.answer(str(exc))
                await state.update_data(input_lock_step=None)
                return
            try:
                await runsteps_repository.upsert([record])
                await _log_step_update(audit_repository, message, record)
                for attachment in attachments:
                    await attachments_repository.add(attachment)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to save step %s: %s", record.step_code, exc)
                await state.update_data(input_lock_step=None)
                await message.answer("Не удалось сохранить шаг, попробуйте ещё раз.")
                return
            if comment_required:
                await state.update_data(pending_comment=record.__dict__)
                await message.answer(
                    "Δ превышает порог. Пожалуйста, введите комментарий для объяснения расхождения."
                )
                await state.update_data(input_lock_step=None)
                return

    if current_idx + 1 >= len(steps):
        await message.answer(
            _final_prompt(data.get("shop_id")),
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return
    next_idx = current_idx + 1
    if current_idx not in completed_steps:
        completed_steps.append(current_idx)
    cleanup: dict[str, Any] = {}
    if is_terminal_step:
        cleanup["terminal_type"] = None
        cleanup["selected_terminal"] = None
    await state.update_data(
        step_index=next_idx,
        completed_steps=completed_steps,
        input_lock_step=None,
        **cleanup,
    )
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


def _effective_owner_role(step: SerializedStep, actor_role: str) -> str:
    """Determine the effective owner_role for RunStep record.

    - "both": both A and B fill this step separately → use actor_role
    - "shared": either A or B can fill → use actor_role
    - "opener"/"closer": specific role → use as-is
    """
    owner = (step.get("owner_role") or "shared").lower()
    if owner in {"shared", "both"}:
        return actor_role
    return owner


def _load_validators(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _final_prompt(shop_id: str | None) -> str:
    suffix = f" /summary {shop_id}" if shop_id else "/summary <shop_id>"
    return f"Все шаги пройдены. Для итога отправьте{suffix}."


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
    owner_role: str,
    terminal_type: str | None = None,
) -> tuple[RunStepRecord, list[AttachmentRecord], bool]:
    step_type = step["type"]
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
    elif step_type == "choice":
        if not message.text:
            raise ValueError("Выберите один из доступных вариантов.")
        raw_value = message.text.strip()
        validators = step.get("validators", {})
        options = validators.get("options") or []
        if step.get("code") == "terminal_choice":
            normalized_choice = _normalize_terminal_choice(raw_value)
        else:
            normalized_choice = _normalize_choice_value(raw_value, options)
        if not normalized_choice:
            options_str = ", ".join(options) if options else "доступных значений"
            raise ValueError(f"Выберите одно из: {options_str}.")
        record = RunStepRecord(
            run_id=run_id,
            phase=phase,
            step_code=step["code"],
            owner_role=owner_role,
            value_text=normalized_choice,
            performer_user_id=performer_user_id,
            status="ok",
        )
    elif step_type == "photo":
        file_id = _extract_photo_file_id(message)
        step_code = step["code"]
        is_terminal_photo = step_code in {
            "photo_terminal",
            "photo_terminal_1",
            "photo_terminal_sber",
            "photo_terminal_tbank",
        }
        if is_terminal_photo:
            if step_code in {"photo_terminal", "photo_terminal_1"} and not terminal_type:
                raise ValueError("Сначала выберите терминал перед загрузкой фото.")
            if not file_id:
                raise ValueError("Отправьте фото сверки (как изображение или документ).")
            # Use terminal_type for kind if provided, else derive from step_code
            if terminal_type:
                kind_suffix = terminal_type.replace(" ", "_")
            elif "sber" in step_code:
                kind_suffix = "Sberbank"
            elif "tbank" in step_code:
                kind_suffix = "T-Bank"
            else:
                kind_suffix = "terminal"
            attachments.append(
                AttachmentRecord(
                    run_id=run_id,
                    step_code=step_code,
                    telegram_file_id=file_id,
                    kind=f"pos_receipt:{owner_role}:{kind_suffix}",
                )
            )
            comment = (message.caption or "").strip()
            record = RunStepRecord(
                run_id=run_id,
                phase=phase,
                step_code=step_code,
                owner_role=owner_role,
                value_text=terminal_type or kind_suffix,
                comment=comment or None,
                performer_user_id=performer_user_id,
                status="ok",
            )
        else:
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


def _normalize_terminal_choice(text: str | None) -> str | None:
    if not text:
        return None
    normalized = text.lower().replace(" ", "").replace("ё", "е")
    if normalized in TERMINAL_CHOICES:
        return TERMINAL_CHOICES[normalized]
    return None


def _normalize_choice_value(text: str, options: list[str]) -> str | None:
    normalized = text.strip().casefold()
    for option in options:
        if normalized == option.strip().casefold():
            return option
    return None


def _build_step_keyboard(step: SerializedStep) -> ReplyKeyboardMarkup:
    if step.get("code") in {"photo_terminal_1", "photo_terminal", "terminal_choice"}:
        buttons = [
            [KeyboardButton(text="Т-Банк"), KeyboardButton(text="Сбербанк")],
            [KeyboardButton(text="Третий терминал")],
            [KeyboardButton(text=t("steps.button.back"))],
        ]
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=False)
    buttons = [[KeyboardButton(text=t("steps.button.back"))]]
    if not step.get("required"):
        buttons.append([KeyboardButton(text=t("steps.button.skip"))])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=False)
