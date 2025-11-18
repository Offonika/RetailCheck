from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

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
    steps_by_role = _group_steps_by_role(steps)
    shop = await find_shop(shops_repository, shop_id)
    dual_mode = bool(shop.dual_cash_mode) if shop else False
    pending = [s.step_code for s in steps if s.status not in {"ok", "skipped"}]
    if pending:
        if dual_mode:
            opener_pending = [
                s.step_code
                for s in steps
                if s.status not in {"ok", "skipped"}
                and (s.owner_role or "shared").lower() in {"opener", "shared"}
            ]
            closer_pending = [
                s.step_code
                for s in steps
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
        if not steps_by_role.get("opener"):
            await message.answer("Нет данных по кассе opener. Попросите открывающего пройти шаги.")
            return
        if not steps_by_role.get("closer"):
            await message.answer("Нет данных по кассе closer. Попросите закрывающего пройти шаги.")
            return
    attachments = await attachments_repository.list_for_run(run.run_id)
    z_photos = [att for att in attachments if att.step_code in {"z_report_photo", "fin_z_photo"}]
    if not z_photos:
        await message.answer("Нельзя завершить смену без Z-фото.")
        return
    missing_comments = _missing_comments(steps)
    if missing_comments:
        await message.answer("Требуются комментарии по шагам: " + ", ".join(missing_comments))
        return
    total_delta = sum(float(step.delta_number) for step in steps if step.delta_number)
    finalized_run = await run_service.finalize_run(run.run_id, total_delta)
    summary = _format_summary(finalized_run, steps, attachments, dual_mode=dual_mode)
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


def _format_summary(run, steps, attachments, *, dual_mode: bool) -> str:
    opener = run.opener_username or run.opener_user_id or "—"
    closer = run.closer_username or run.closer_user_id or "—"
    total_delta = sum(float(step.delta_number) for step in steps if step.delta_number)
    z_photos = [att for att in attachments if att.step_code in {"z_report_photo", "fin_z_photo"}]
    z_status = "есть" if z_photos else "нет"
    lines = [
        f"Итог смены {run.shop_id} / {run.date}",
        f"Открыл: {opener}",
        f"Закрыл: {closer}",
        f"Общее отклонение: {total_delta:.2f}",
        f"Z-фото: {z_status}",
    ]
    grouped = _group_steps_by_role(steps)
    if dual_mode:
        lines.append("Касса opener:")
        lines.extend(_format_step_lines(grouped.get("opener", [])))
        lines.append("Касса closer:")
        lines.extend(_format_step_lines(grouped.get("closer", [])))
        if grouped.get("shared"):
            lines.append("Общие шаги:")
            lines.extend(_format_step_lines(grouped.get("shared", [])))
    else:
        lines.append("Детализация:")
        lines.extend(_format_step_lines(steps))
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


def _format_step_lines(step_list):
    if not step_list:
        return ["- —"]
    lines = []
    for step in step_list:
        value = step.value_number or step.value_text or step.value_check or "—"
        delta = step.delta_number or "—"
        lines.append(f"- {step.step_code}: {value} (Δ={delta})")
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
    return (
        f"- {shop_name} ({shop_id}): {run.status}, "
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
