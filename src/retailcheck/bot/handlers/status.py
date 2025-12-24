from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.models import AuditRecord
from retailcheck.bot.utils.access import find_shop
from retailcheck.bot.utils.notify import broadcast_to_targets, collect_shop_chat_ids
from retailcheck.export.repository import ExportRepository
from retailcheck.export.utils import append_export_record
from retailcheck.runs.repository import RunsRepository
from retailcheck.runs.service import RunService
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.shops.repository import ShopsRepository
from retailcheck.templates.repository import TemplateRepository
from retailcheck.users.repository import UsersRepository

router = Router()


@router.message(Command("status"))
async def run_status(
    message: Message,
    run_service: RunService,
    runsteps_repository: RunStepsRepository,
    shops_repository: ShopsRepository,
) -> None:
    shop_id = _extract_shop_id(message.text)
    if shop_id:
        await _send_single_status(message, run_service, runsteps_repository, shop_id)
        return

    shops = await shops_repository.list_active()
    today = date.today().isoformat()
    lines = [f"Статусы смен на {today}:"]
    for shop in shops:
        run = await run_service.get_today_run(shop.shop_id)
        if not run:
            lines.append(f"- {shop.name} ({shop.shop_id}): нет смены.")
            continue
        steps = await runsteps_repository.list_for_run(run.run_id)
        lines.append(_format_shop_overview(shop.name, shop.shop_id, run, steps))
    await message.answer("\n".join(lines))


@router.message(Command("summary"))
async def run_summary(
    message: Message,
    run_service: RunService,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    audit_repository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository,
    users_repository: UsersRepository,
    template_repository: TemplateRepository,
) -> None:
    shop_id = _extract_shop_id(message.text) or "shop_1"
    user = message.from_user
    if not user:
        await message.answer(
            "Не удалось определить пользователя. Повторите команду из личного чата."
        )
        return
    run = await run_service.get_today_run(shop_id)
    if not run:
        await message.answer(f"Для магазина {shop_id} сегодня смена не создана.")
        return
    if run.current_active_user_id and str(user.id) != run.current_active_user_id:
        active_name = (
            run.closer_username
            if run.closer_user_id == run.current_active_user_id and run.closer_username
            else run.opener_username
            if run.opener_user_id == run.current_active_user_id and run.opener_username
            else run.current_active_user_id
        )
        await message.answer(
            "Закрыть смену может только активный сотрудник. "
            f"Сейчас активен: {active_name}. Нажмите «Продолжить смену», чтобы стать активным."
        )
        return
    steps = await runsteps_repository.list_for_run(run.run_id)
    shop = await find_shop(shops_repository, shop_id)
    dual_mode = bool(shop.dual_cash_mode) if shop else False
    requirements = _collect_step_requirements(run, template_repository)
    pending = [s.step_code for s in steps if s.status not in {"ok", "skipped"}]
    grouped = _group_steps_by_role(steps)
    opener_steps = grouped.get("opener") or grouped.get("shared")
    closer_steps = grouped.get("closer") or grouped.get("shared")
    closer_day_started = any(
        (s.owner_role or "").lower() == "closer" and s.phase != "close" for s in steps
    )
    require_closer_day_steps = closer_day_started or (
        run.closer_user_id
        and run.opener_user_id
        and run.closer_user_id != run.opener_user_id
    )
    if pending:
        if dual_mode:
            opener_pending = [
                s.step_code
                for s in opener_steps or []
                if s.status not in {"ok", "skipped"}
                and (s.owner_role or "shared").lower() in {"opener", "shared"}
            ]
            closer_pending = [
                s.step_code
                for s in closer_steps or []
                if s.status not in {"ok", "skipped"}
                and (s.owner_role or "shared").lower() in {"closer", "shared"}
            ]
            if opener_pending:
                await message.answer(
                    "Нельзя сформировать итог: откройте все шаги кассы opener: "
                    + ", ".join(opener_pending)
                )
            elif closer_pending:
                await message.answer(
                    "Нельзя сформировать итог: завершите шаги кассы closer: "
                    + ", ".join(closer_pending)
                )
            else:
                await message.answer(
                    "Нельзя сформировать итог: требуется завершить шаги " + ", ".join(pending)
                )
        else:
            await message.answer(
                "Нельзя сформировать итог: требуется завершить шаги " + ", ".join(pending)
            )
        return
    if dual_mode:
        if not opener_steps:
            await message.answer("Нет данных по кассе opener. Попросите открывающего пройти шаги.")
            return
        if not closer_steps and require_closer_day_steps:
            await message.answer("Нет данных по кассе closer. Попросите закрывающего пройти шаги.")
            return
    missing_required = _missing_required_steps(
        requirements, steps, require_closer_day_steps=require_closer_day_steps
    )
    if missing_required:
        await message.answer(
            "Нельзя сформировать итог: обязательные шаги не закрыты → "
            + _format_missing_required(missing_required, requirements)
        )
        return
    attachments = await attachments_repository.list_for_run(run.run_id)
    z_photos = [
        att
        for att in attachments
        if att.step_code in {"z_report_photo", "fin_z_photo", "photo_z_report"}
    ]
    if not z_photos:
        await message.answer("Нельзя завершить смену без Z-фото.")
        return
    missing_comments = _missing_comments(steps)
    if missing_comments:
        await message.answer("Требуются комментарии по шагам: " + ", ".join(missing_comments))
        return
    total_delta = sum(float(step.delta_number) for step in steps if step.delta_number)
    conditional_errors = _check_conditional_requirements(
        requirements,
        steps,
        attachments,
        total_delta,
    )
    if conditional_errors:
        await message.answer("Нельзя завершить смену: " + " ".join(conditional_errors))
        return
    await run_service.mark_ready_to_close(run.run_id)
    finalized_run = await run_service.finalize_run(run.run_id, total_delta)
    summary = _format_summary(
        finalized_run,
        steps,
        attachments,
        dual_mode=dual_mode,
        requirements=requirements,
        grouped_override=grouped if dual_mode else None,
    )
    await message.answer(summary, disable_web_page_preview=True)
    await _log_audit(audit_repository, message.from_user, finalized_run, summary)
    await append_export_record(
        finalized_run,
        runsteps_repository,
        attachments_repository,
        export_repository,
        shops_repository=shops_repository,
        steps=steps,
        attachments=attachments,
    )
    bot = message.bot
    if bot is None:
        await message.answer("Не удалось получить экземпляр бота для уведомлений.")
        return
    dispatcher: Any = getattr(bot, "dispatcher", None)
    manager_ids: list[int] = []
    if dispatcher and hasattr(dispatcher, "get"):
        manager_ids = dispatcher.get("manager_notify_chat_ids", [])
    shop_chat_ids = await collect_shop_chat_ids(
        shop_id,
        shops_repository,
        users_repository,
    )
    await broadcast_to_targets(
        bot,
        summary,
        manager_ids,
        shop_chat_ids,
        disable_preview=True,
    )


@router.message(Command("export"))
async def run_export(
    message: Message,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository,
) -> None:
    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer("Использование: /export shop_id [YYYY-MM-DD]")
        return
    _, shop_id, *rest = args
    target_date = rest[0] if rest else date.today().isoformat()
    run = await runs_repository.get_run(shop_id, target_date)
    if not run:
        await message.answer(f"Смена {shop_id} за {target_date} не найдена.")
        return
    record, total_delta = await append_export_record(
        run,
        runsteps_repository,
        attachments_repository,
        export_repository,
        shops_repository=shops_repository,
    )
    record.period_start = target_date
    record.period_end = target_date
    totals_preview = json.dumps(json.loads(record.totals_json), ensure_ascii=False)[:200]
    await message.answer(
        "Экспорт сформирован:\n"
        f"- магазин: {shop_id}\n"
        f"- дата: {target_date}\n"
        f"- статус: {run.status}\n"
        f"- Δ={total_delta:+.2f}\n"
        f"- totals: {totals_preview}"
    )


@router.message(Command("export_week"))
async def run_export_week(
    message: Message,
    runs_repository: RunsRepository,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository,
) -> None:
    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer("Использование: /export_week shop_id [YYYY-MM-DD]")
        return
    _, shop_id, *rest = args
    reference = date.today()
    if rest:
        try:
            reference = date.fromisoformat(rest[0])
        except ValueError:
            await message.answer("Дата должна быть в формате YYYY-MM-DD.")
            return
    runs = await runs_repository.list_runs()
    start = reference - timedelta(days=6)
    exported: list[tuple[str, float]] = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        run = next((r for r in runs if r.shop_id == shop_id and r.date == day.isoformat()), None)
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
        await message.answer("Не найдено смен за указанный период.")
        return
    details = "\n".join(f"- {day}: Δ={delta:+.2f}" for day, delta in exported)
    await message.answer(
        "Экспорт за неделю готов:\n"
        f"Магазин: {shop_id}, период {start.isoformat()} – {reference.isoformat()}\n"
        f"{details}"
    )


def _extract_shop_id(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


async def _send_single_status(
    message: Message,
    run_service: RunService,
    runsteps_repository: RunStepsRepository,
    shop_id: str,
) -> None:
    run = await run_service.get_today_run(shop_id)
    if not run:
        await message.answer(f"Для магазина {shop_id} сегодня смена не создана.")
        return
    steps = await runsteps_repository.list_for_run(run.run_id)
    text = _format_status(run, steps)
    await message.answer(text)


def _format_status(run, steps) -> str:
    if isinstance(steps, int):
        steps_count = steps
        completed = steps
        delta = 0.0
        pending: list[str] = []
    else:
        steps_count = len(steps)
        completed = len([s for s in steps if s.status in {"ok", "skipped"}])
        delta = _calc_delta(steps)
        pending = [s.step_code for s in steps if s.status not in {"ok", "skipped"}]
    opener = run.opener_username or run.opener_user_id or "—"
    closer = run.closer_username or run.closer_user_id or "—"
    return (
        f"Смена {run.shop_id} / {run.date}\n"
        f"Статус: {run.status}\n"
        f"Открытие: {opener}\n"
        f"Закрытие: {closer}\n"
        f"Шаги: {completed}/{steps_count}\n"
        f"Текущая дельта: {delta:+.2f}\n"
        f"Осталось: {', '.join(pending) if pending else 'всё готово'}"
    )


def _format_summary(
    run,
    steps,
    attachments,
    *,
    dual_mode: bool,
    requirements: dict[str, StepRequirementMeta] | None = None,
    grouped_override: dict[str, list] | None = None,
) -> str:
    requirements = requirements or {}
    opener = run.opener_username or run.opener_user_id or "—"
    closer = run.closer_username or run.closer_user_id or "—"
    total_delta = sum(float(step.delta_number) for step in steps if step.delta_number)
    z_photos = [att for att in attachments if att.step_code in {"z_report_photo", "fin_z_photo"}]
    z_status = "есть" if z_photos else "нет"
    cash_total = _aggregate_step_totals(steps, {"cash"})
    noncash_total = _aggregate_step_totals(
        steps,
        {"noncash", "pos_sber", "pos_tbank", "sber", "tbank"},
    )
    lines = [
        f"Итог смены {run.shop_id} / {run.date}",
        f"Открыл: {opener}",
        f"Закрыл: {closer}",
        f"Общее отклонение: {total_delta:.2f}",
        f"Z-фото: {z_status}",
    ]
    if cash_total or noncash_total:
        aggregate_line = "Агрегаты:"
        if cash_total:
            aggregate_line += f" наличные={cash_total}"
        if noncash_total:
            aggregate_line += f", эквайринг={noncash_total}"
        lines.append(aggregate_line)
    grouped = grouped_override or _group_steps_by_role(steps)
    # In dual mode допускаем использование shared шагов, если нет явных блоков по роли.
    opener_steps = grouped.get("opener") or grouped.get("shared") or []
    closer_steps = grouped.get("closer") or grouped.get("shared") or []
    if dual_mode:
        lines.append("Касса opener:")
        lines.extend(_format_step_lines(opener_steps, requirements))
        lines.append("Касса closer:")
        lines.extend(_format_step_lines(closer_steps, requirements))
        if grouped.get("shared"):
            lines.append("Общие шаги:")
            lines.extend(_format_step_lines(grouped.get("shared", []), requirements))
    else:
        lines.append("Детализация:")
        lines.extend(_format_step_lines(steps, requirements))
    attachment_lines = _format_attachments_summary(attachments, requirements)
    if attachment_lines:
        lines.append("Фото/файлы:")
        lines.extend(attachment_lines)
    return "\n".join(lines)


def _group_steps_by_role(steps):
    buckets: dict[str, list] = {
        "opener": [],
        "closer": [],
        "shared": [],
    }
    for step in steps:
        role = (step.owner_role or "shared").lower()
        buckets.setdefault(role, []).append(step)
    return buckets


def _format_step_lines(step_list, requirements):
    if not step_list:
        return ["- —"]
    lines = []
    for step in step_list:
        meta = requirements.get(step.step_code)
        title = meta.title if meta else step.step_code
        value = step.value_number or step.value_text or step.value_check or "—"
        delta = step.delta_number or "—"
        comment = f", комментарий: {step.comment}" if step.comment else ""
        role_note = ""
        if step.step_code == "photo_terminal_1":
            role_label = _pretty_role(step.owner_role)
            if role_label:
                role_note = f" — {role_label}"
        title_with_role = f"{title}{role_note}"
        lines.append(f"- {title_with_role}: {value} (Δ={delta}{comment})")
    return lines


def _missing_comments(steps) -> list[str]:
    missing = []
    for step in steps:
        if step.delta_number and not step.comment:
            missing.append(step.step_code)
    return missing


def _format_shop_overview(shop_name, shop_id, run, steps) -> str:
    total_steps = len(steps)
    completed = len([s for s in steps if s.status in {"ok", "skipped"}])
    pending_codes = [s.step_code for s in steps if s.status not in {"ok", "skipped"}]
    delta = _calc_delta(steps)
    opener = run.opener_username or run.opener_user_id or "—"
    closer = run.closer_username or run.closer_user_id or "—"
    pending_text = ", ".join(pending_codes[:3]) if pending_codes else "нет"
    if len(pending_codes) > 3:
        pending_text += f" … +{len(pending_codes) - 3}"
    status_text = run.status
    if run.status == "returned" and run.comment:
        status_text += f" (вернули: {run.comment})"
    return (
        f"- {shop_name} ({shop_id}): {status_text}, "
        f"Откр: {opener}, Закр: {closer}, "
        f"Шаги: {completed}/{total_steps}, Δ={delta:+.2f}, "
        f"Ждём: {pending_text}. "
        f"/status {shop_id} для деталей"
    )


def _calc_delta(steps) -> float:
    total = 0.0
    for step in steps:
        if step.delta_number:
            try:
                total += float(step.delta_number)
            except ValueError:
                continue
    return total


def _aggregate_step_totals(steps, include_tokens: set[str]) -> str | None:
    total = 0.0
    found = False
    for step in steps:
        code = (step.step_code or "").lower()
        if not any(token in code for token in include_tokens):
            continue
        value = step.value_number or step.value_text
        if not value:
            continue
        try:
            total += float(value)
            found = True
        except ValueError:
            continue
    return f"{total:.2f}" if found else None


def _format_attachments_summary(attachments, requirements):
    if not attachments:
        return []
    counters: dict[tuple[str, str], int] = {}
    for attachment in attachments:
        meta = requirements.get(attachment.step_code)
        role = _attachment_owner_role(attachment, meta)
        key = (attachment.step_code, role)
        counters[key] = counters.get(key, 0) + 1
    lines = []
    for (code, role), count in counters.items():
        meta = requirements.get(code)
        title = meta.title if meta else code
        role_note = f" — {_pretty_role(role)}" if role and role != "shared" else ""
        lines.append(f"- {title}{role_note}: {count} шт.")
    return lines


async def _log_audit(audit_repo, user, run, summary: str) -> None:
    record = AuditRecord.create(
        action="finish_close",
        entity="run",
        entity_id=run.run_id,
        details=summary,
        user_id=str(user.id) if user else None,
    )
    await audit_repo.append(record)


async def _notify_manager(bot, chat_ids: list[int], summary: str) -> None:
    await broadcast_to_targets(bot, summary, chat_ids, disable_preview=True)


@dataclass(frozen=True)
class StepRequirementMeta:
    code: str
    title: str
    owner_role: str
    required: bool
    step_type: str
    validators: dict[str, Any] = field(default_factory=dict)
    phase: str = "open"
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def owner_roles(self) -> set[str]:
        """Return normalized set of roles that should satisfy the step."""
        base = set(self.roles) if self.roles else {self.owner_role or "shared"}
        expanded: set[str] = set()
        for role in base:
            if role == "both":
                expanded.update({"opener", "closer"})
            else:
                expanded.add(role or "shared")
        return expanded


def _collect_step_requirements(
    run,
    template_repository: TemplateRepository,
) -> dict[str, StepRequirementMeta]:
    requirements: dict[str, StepRequirementMeta] = {}
    phase_map = dict(run.template_phase_map or {})
    if not phase_map.get("open") and run.template_open_id:
        phase_map["open"] = run.template_open_id
    if not phase_map.get("close") and run.template_close_id:
        phase_map["close"] = run.template_close_id
    for template_id in phase_map.values():
        if not template_id:
            continue
        try:
            template = template_repository.get(template_id)
        except KeyError:
            logger.warning("Template %s not found in repository", template_id)
            continue
        for step in template.steps:
            owner = (step.owner_role or "shared").lower()
            owner_roles = {"opener", "closer"} if owner == "both" else {owner}
            validators = _parse_validators(step.validators_json)
            existing = requirements.get(step.code)
            if existing:
                merged_roles = existing.owner_roles | owner_roles
                merged_validators = {**existing.validators, **validators}
                required = existing.required or bool(step.required)
                title = existing.title or step.title
                step_type = existing.step_type or step.type
                phase_value = existing.phase
            else:
                merged_roles = owner_roles
                merged_validators = validators
                required = bool(step.required)
                title = step.title
                step_type = step.type
                phase_value = template.phase
            requirements[step.code] = StepRequirementMeta(
                code=step.code,
                title=title,
                owner_role=_normalize_owner_role(merged_roles),
                required=required,
                step_type=step_type,
                phase=phase_value,
                validators=merged_validators,
                roles=tuple(sorted(merged_roles)),
            )
    return requirements


def _parse_validators(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _normalize_owner_role(roles: set[str]) -> str:
    cleaned = {role or "shared" for role in roles}
    if cleaned == {"opener", "closer"}:
        return "both"
    if len(cleaned) == 1:
        return next(iter(cleaned))
    return "shared"


def _owners_to_check(meta: StepRequirementMeta, require_closer_day_steps: bool) -> list[str]:
    roles = set(meta.owner_roles)
    if not require_closer_day_steps and meta.phase != "close":
        roles.discard("closer")
    if meta.code in {"photo_terminal_1", "photo_terminal"} and (
        "opener" in roles or "closer" in roles or meta.owner_role == "shared"
    ):
        return [
            role
            for role in ("opener", "closer")
            if role in roles or meta.owner_role == "shared"
        ]
    if roles == {"shared"}:
        return ["shared"]
    return sorted(roles)


def _pick_step_record(step_map: dict[tuple[str, str], list], code: str, owner: str):
    if owner == "shared":
        for candidate in ("shared", "opener", "closer", "any"):
            records = step_map.get((code, candidate))
            if records:
                return records[-1]
        return None
    records = step_map.get((code, owner))
    return records[-1] if records else None


def _missing_required_steps(
    requirements: dict[str, StepRequirementMeta],
    steps: Iterable,
    *,
    require_closer_day_steps: bool = True,
) -> dict[str, list[str]]:
    step_map: dict[tuple[str, str], list] = {}
    for step in steps:
        owner = (step.owner_role or "shared").lower()
        step_map.setdefault((step.step_code, owner), []).append(step)
        step_map.setdefault((step.step_code, "any"), []).append(step)
    missing: dict[str, list[str]] = {}
    for code, meta in requirements.items():
        if not meta.required:
            continue
        for owner in _owners_to_check(meta, require_closer_day_steps):
            record = _pick_step_record(step_map, code, owner)
            if not record or record.status != "ok":
                missing.setdefault(owner, []).append(code)
    return missing


def _format_missing_required(
    missing: dict[str, list[str]],
    requirements: dict[str, StepRequirementMeta],
) -> str:
    labels = {
        "opener": "opener",
        "closer": "closer",
        "shared": "shared",
    }
    parts = []
    for owner, codes in missing.items():
        titles = []
        for code in codes:
            req = requirements.get(code)
            titles.append(req.title if req else code)
        label = labels.get(owner, owner or "shared")
        parts.append(f"{label}: " + ", ".join(titles))
    return "; ".join(parts)


def _attachment_owner_role(attachment, meta: StepRequirementMeta | None) -> str:
    kind_raw = (attachment.kind or "").strip()
    parts = [part for part in kind_raw.split(":") if part]
    if len(parts) > 1 and parts[1] in {"opener", "closer"}:
        return parts[1]
    if meta:
        explicit_roles = [role for role in meta.owner_roles if role in {"opener", "closer"}]
        if len(explicit_roles) == 1:
            return explicit_roles[0]
    return "shared"


def _pretty_role(owner_role: str | None) -> str:
    if not owner_role:
        return ""
    mapping = {"opener": "A", "closer": "B"}
    return mapping.get(owner_role, owner_role)


def _check_conditional_requirements(
    requirements: dict[str, StepRequirementMeta],
    steps,
    attachments,
    total_delta: float,
) -> list[str]:
    abs_delta = abs(total_delta)
    if abs_delta == 0:
        return []
    step_map: dict[str, list] = {}
    for step in steps:
        step_map.setdefault(step.step_code, []).append(step)
    errors: list[str] = []
    for meta in requirements.values():
        validators = meta.validators
        threshold_raw = validators.get("delta_threshold")
        if not threshold_raw:
            continue
        try:
            threshold = float(threshold_raw)
        except (TypeError, ValueError):
            continue
        if not threshold or abs_delta < threshold:
            continue
        pretty_title = meta.title or meta.code
        if meta.step_type == "text":
            records = step_map.get(meta.code, [])
            has_value = any((record.value_text or "").strip() for record in records)
            if not has_value:
                errors.append(
                    f"требуется комментарий в шаге «{pretty_title}» при Δ ≥ {threshold:.0f} ₽."
                )
        elif meta.step_type == "photo":
            has_photo = any(att.step_code == meta.code for att in attachments)
            if not has_photo:
                errors.append(
                    f"требуется фото «{pretty_title}» при Δ ≥ {threshold:.0f} ₽."
                )
    return errors
