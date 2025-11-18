from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python

ATTACHMENT_HEADERS = [
    "run_id",
    "step_code",
    "telegram_file_id",
    "kind",
    "created_at",
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AttachmentRecord:
    run_id: str
    step_code: str
    telegram_file_id: str
    kind: str = "other"
    created_at: str = now_iso()

    def to_row(self) -> list[str]:
        return [
            self.run_id,
            self.step_code,
            self.telegram_file_id,
            self.kind,
            self.created_at,
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> AttachmentRecord:
        padded = row + [""] * (len(ATTACHMENT_HEADERS) - len(row))
        return cls(
            run_id=padded[0],
            step_code=padded[1],
            telegram_file_id=padded[2],
            kind=padded[3] or "other",
            created_at=padded[4] or now_iso(),
        )
