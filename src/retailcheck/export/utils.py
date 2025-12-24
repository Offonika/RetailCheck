from __future__ import annotations

from collections.abc import Sequence

from retailcheck.attachments.models import AttachmentRecord
from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.export.models import ExportRecord
from retailcheck.export.repository import ExportRepository
from retailcheck.runsteps.models import RunStepRecord
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.shops.repository import ShopsRepository


async def append_export_record(
    run,
    runsteps_repository: RunStepsRepository,
    attachments_repository: AttachmentRepository,
    export_repository: ExportRepository,
    shops_repository: ShopsRepository | None = None,
    steps: Sequence[RunStepRecord] | None = None,
    attachments: Sequence[AttachmentRecord] | None = None,
) -> tuple[ExportRecord, float]:
    steps = (
        list(steps)
        if steps is not None
        else list(await runsteps_repository.list_for_run(run.run_id))
    )
    attachments = (
        list(attachments)
        if attachments is not None
        else list(await attachments_repository.list_for_run(run.run_id))
    )
    total_delta = sum(float(step.delta_number) for step in steps if step.delta_number)
    shop_name = await _resolve_shop_name(run.shop_id, shops_repository)
    cash_total = _aggregate_steps(steps, include_tokens={"cash"}, exclude_tokens={"noncash"})
    noncash_total = _aggregate_steps(
        steps, include_tokens={"non_cash", "noncash", "sberbank", "tbank"}
    )
    delta_comment = (
        "; ".join(step.comment.strip() for step in steps if step.comment and step.delta_number)
        or None
    )
    record = ExportRecord.from_summary(
        run,
        steps,
        attachments,
        total_delta,
        shop_name=shop_name,
        cash_total=cash_total,
        noncash_total=noncash_total,
        delta_comment=delta_comment,
        audit_link=run.run_id,
    )
    await export_repository.append(record)
    return record, total_delta


async def _resolve_shop_name(
    shop_id: str,
    shops_repository: ShopsRepository | None,
) -> str:
    if not shops_repository:
        return shop_id
    shops = await shops_repository.list_active()
    for shop in shops:
        if shop.shop_id == shop_id:
            return shop.name
    return shop_id


def _aggregate_steps(
    steps: Sequence[RunStepRecord],
    include_tokens: set[str],
    exclude_tokens: set[str] | None = None,
) -> str | None:
    total = 0.0
    found = False
    for step in steps:
        code = step.step_code.lower()
        if not any(token in code for token in include_tokens):
            continue
        if exclude_tokens and any(token in code for token in exclude_tokens):
            continue
        value = step.value_number or step.value_text or step.value_check
        if not value:
            continue
        try:
            total += float(value)
            found = True
        except ValueError:
            continue
    return f"{total:.2f}" if found else None
