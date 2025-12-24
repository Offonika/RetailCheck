from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python

RUN_STEP_HEADERS = [
    "run_id",
    "phase",
    "step_code",
    "owner_role",
    "value_number",
    "value_text",
    "value_check",
    "delta_number",
    "comment",
    "performer_user_id",
    "status",
    "started_at",
    "updated_at",
    "idempotency_key",
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunStepRecord:
    run_id: str
    phase: str
    step_code: str
    owner_role: str = "shared"
    value_number: str | None = None
    value_text: str | None = None
    value_check: str | None = None
    delta_number: str | None = None
    comment: str | None = None
    performer_user_id: str | None = None
    status: str = "pending"
    started_at: str = now_iso()
    updated_at: str = now_iso()
    idempotency_key: str | None = None

    def to_row(self) -> list[str]:
        return [
            self.run_id,
            self.phase,
            self.step_code,
            self.owner_role,
            self.value_number or "",
            self.value_text or "",
            self.value_check or "",
            self.delta_number or "",
            self.comment or "",
            self.performer_user_id or "",
            self.status,
            self.started_at,
            self.updated_at,
            self.idempotency_key or "",
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> RunStepRecord:
        padded = row + [""] * (len(RUN_STEP_HEADERS) - len(row))
        started_idx = RUN_STEP_HEADERS.index("started_at")
        updated_idx = RUN_STEP_HEADERS.index("updated_at")
        idempotency_idx = RUN_STEP_HEADERS.index("idempotency_key")
        return cls(
            run_id=padded[0],
            phase=padded[1],
            step_code=padded[2],
            owner_role=padded[3] or "shared",
            value_number=padded[4] or None,
            value_text=padded[5] or None,
            value_check=padded[6] or None,
            delta_number=padded[7] or None,
            comment=padded[8] or None,
            performer_user_id=padded[9] or None,
            status=padded[10] or "pending",
            started_at=padded[started_idx] or now_iso(),
            updated_at=padded[updated_idx] or now_iso(),
            idempotency_key=padded[idempotency_idx] or None,
        )
