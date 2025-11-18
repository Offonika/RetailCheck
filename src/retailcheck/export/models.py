from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from retailcheck.attachments.models import AttachmentRecord
from retailcheck.runsteps.models import RunStepRecord

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ExportRecord:
    export_id: str
    period_start: str
    period_end: str
    shop_id: str
    shop_name: str
    run_id: str
    run_date: str
    status: str
    opener_user_id: str | None
    opener_username: str | None
    opener_at: str | None
    closer_user_id: str | None
    closer_username: str | None
    closer_at: str | None
    totals_json: str
    cash_total: str | None
    noncash_total: str | None
    delta_total: str
    delta_comment: str | None
    comment: str | None
    attachments_summary: str
    audit_link: str | None
    generated_at: str

    def to_row(self) -> list[str]:
        return [
            self.export_id,
            self.period_start,
            self.period_end,
            self.shop_id,
            self.shop_name,
            self.run_id,
            self.run_date,
            self.status,
            self.opener_user_id or "",
            self.opener_username or "",
            self.opener_at or "",
            self.closer_user_id or "",
            self.closer_username or "",
            self.closer_at or "",
            self.totals_json,
            self.cash_total or "",
            self.noncash_total or "",
            self.delta_total,
            self.delta_comment or "",
            self.comment or "",
            self.attachments_summary,
            self.audit_link or "",
            self.generated_at,
        ]

    @classmethod
    def from_summary(
        cls,
        run,
        steps: Sequence[RunStepRecord],
        attachments: Sequence[AttachmentRecord],
        delta_total: float,
        shop_name: str,
        cash_total: str | None,
        noncash_total: str | None,
        delta_comment: str | None,
        audit_link: str | None = None,
    ) -> ExportRecord:
        return cls(
            export_id=str(uuid4()),
            period_start=run.date,
            period_end=run.date,
            shop_id=run.shop_id,
            shop_name=shop_name,
            run_id=run.run_id,
            run_date=run.date,
            status=run.status,
            opener_user_id=run.opener_user_id,
            opener_username=run.opener_username,
            opener_at=run.opener_at,
            closer_user_id=run.closer_user_id,
            closer_username=run.closer_username,
            closer_at=run.closer_at,
            totals_json=_serialize_totals(steps),
            cash_total=cash_total,
            noncash_total=noncash_total,
            delta_total=f"{delta_total:.2f}",
            delta_comment=delta_comment,
            comment=run.comment,
            attachments_summary=_format_attachments_summary(attachments, steps),
            audit_link=audit_link,
            generated_at=now_iso(),
        )


def _serialize_totals(steps: Sequence[RunStepRecord]) -> str:
    totals: dict[str, dict[str, str]] = {}
    for step in steps:
        value = step.value_number or step.value_text or step.value_check
        if value is None:
            continue
        role = (step.owner_role or "shared").lower()
        role_totals = totals.setdefault(role, {})
        role_totals[step.step_code] = str(value)
    # сортируем для стабильности
    ordered = {
        role: {code: role_totals[code] for code in sorted(role_totals)}
        for role, role_totals in sorted(totals.items())
    }
    return json.dumps(ordered, ensure_ascii=False, sort_keys=True)


def _format_attachments_summary(
    attachments: Sequence[AttachmentRecord],
    steps: Sequence[RunStepRecord],
) -> str:
    if not attachments:
        return ""
    role_by_step = {step.step_code: step.owner_role or "shared" for step in steps}
    entries: list[str] = []
    for att in attachments:
        descriptor = att.step_code
        kind = (att.kind or "").strip()
        if kind:
            descriptor = f"{descriptor}:{kind}"
        role_prefix = role_by_step.get(att.step_code, "shared")
        entries.append(f"{role_prefix}:{descriptor}={att.telegram_file_id}")
    entries.sort()
    return ", ".join(entries)
