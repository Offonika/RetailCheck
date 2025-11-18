from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditRecord:
    ts: str
    user_id: str | None
    action: str
    entity: str
    entity_id: str
    details: str

    def to_row(self) -> list[str]:
        return [
            self.ts,
            self.user_id or "",
            self.action,
            self.entity,
            self.entity_id,
            self.details,
        ]

    @classmethod
    def create(
        cls,
        action: str,
        entity: str,
        entity_id: str,
        details: str,
        user_id: str | None = None,
    ) -> AuditRecord:
        return cls(
            ts=now_iso(),
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
        )
