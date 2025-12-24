from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017 - keep fallback for older Python

RUN_HEADERS = [
    "run_id",
    "date",
    "shop_id",
    "status",
    "opener_user_id",
    "opener_username",
    "opener_at",
    "closer_user_id",
    "closer_username",
    "closer_at",
    "current_active_user_id",
    "template_open_id",
    "template_close_id",
    "template_phase_map",
    "delta_rub",
    "comment",
    "version",
    "created_at",
    "finished_at",
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunRecord:
    run_id: str
    date: str
    shop_id: str
    status: str
    opener_user_id: str | None = None
    opener_username: str | None = None
    opener_at: str | None = None
    closer_user_id: str | None = None
    closer_username: str | None = None
    closer_at: str | None = None
    current_active_user_id: str | None = None
    template_open_id: str = ""
    template_close_id: str = ""
    template_phase_map: dict[str, str] = field(default_factory=dict)
    delta_rub: str | None = None
    comment: str | None = None
    version: int = 1
    created_at: str = field(default_factory=now_iso)
    finished_at: str | None = None

    def __post_init__(self) -> None:
        if self.template_phase_map is None:
            self.template_phase_map = {}
        if self.template_open_id:
            self.template_phase_map.setdefault("open", self.template_open_id)
        elif template := self.template_phase_map.get("open"):
            self.template_open_id = template
        if self.template_close_id:
            self.template_phase_map.setdefault("close", self.template_close_id)
        elif template := self.template_phase_map.get("close"):
            self.template_close_id = template

    def to_row(self) -> list[str]:
        phase_map_str = (
            json.dumps(self.template_phase_map, ensure_ascii=False, separators=(",", ":"))
            if self.template_phase_map
            else ""
        )
        return [
            self.run_id,
            self.date,
            self.shop_id,
            self.status,
            self.opener_user_id or "",
            self.opener_username or "",
            self.opener_at or "",
            self.closer_user_id or "",
            self.closer_username or "",
            self.closer_at or "",
            self.current_active_user_id or "",
            self.template_open_id,
            self.template_close_id,
            phase_map_str,
            self.delta_rub or "",
            self.comment or "",
            str(self.version),
            self.created_at,
            self.finished_at or "",
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> RunRecord:
        expected_len = len(RUN_HEADERS)
        original_len = len(row)
        if original_len >= expected_len:
            padded = row + [""] * (expected_len - original_len)
            current_active_idx = 10
            template_open_idx = 11
            template_close_idx = 12
            phase_map_idx = 13
            delta_idx = 14
            comment_idx = 15
            version_idx = 16
            created_idx = 17
            finished_idx = 18
            phase_map_raw = padded[phase_map_idx]
        elif original_len == expected_len - 1:
            padded = row + [""]
            # Heuristic: if column 11 looks like a template id (e.g. opening_v1), then the
            # row comes from the older layout without current_active_user_id. Otherwise
            # we assume only finished_at is missing.
            looks_like_template = padded[10] and not padded[10].isdigit()
            if looks_like_template:
                current_active_idx = None
                template_open_idx = 10
                template_close_idx = 11
                phase_map_idx = 12
                delta_idx = 13
                comment_idx = 14
                version_idx = 15
                created_idx = 16
                finished_idx = 17
            else:
                current_active_idx = 10
                template_open_idx = 11
                template_close_idx = 12
                phase_map_idx = 13
                delta_idx = 14
                comment_idx = 15
                version_idx = 16
                created_idx = 17
                finished_idx = 18
            phase_map_raw = padded[phase_map_idx]
        else:
            padded = row + [""] * ((expected_len - 2) - original_len)
            current_active_idx = None
            template_open_idx = 10
            template_close_idx = 11
            phase_map_raw = ""
            delta_idx = 12
            comment_idx = 13
            version_idx = 14
            created_idx = 15
            finished_idx = 16
        return cls(
            run_id=padded[0],
            date=padded[1],
            shop_id=padded[2],
            status=padded[3] or "opened",
            opener_user_id=padded[4] or None,
            opener_username=padded[5] or None,
            opener_at=padded[6] or None,
            closer_user_id=padded[7] or None,
            closer_username=padded[8] or None,
            closer_at=padded[9] or None,
            current_active_user_id=(
                (padded[current_active_idx] or None) if current_active_idx is not None else None
            ),
            template_open_id=padded[template_open_idx] or "",
            template_close_id=padded[template_close_idx] or "",
            template_phase_map=_parse_phase_map(phase_map_raw),
            delta_rub=padded[delta_idx] or None,
            comment=padded[comment_idx] or None,
            version=int(padded[version_idx] or "1"),
            created_at=padded[created_idx] or now_iso(),
            finished_at=padded[finished_idx] or None,
        )

    def with_opener(
        self,
        user_id: str,
        username: str | None,
        *,
        preserve_status: bool = False,
    ) -> RunRecord:
        self.opener_user_id = user_id
        self.opener_username = username
        self.opener_at = now_iso()
        if not preserve_status and self.status != "closed":
            self.status = "in_progress"
        return self

    def with_closer(
        self,
        user_id: str,
        username: str | None,
        *,
        preserve_status: bool = False,
    ) -> RunRecord:
        self.closer_user_id = user_id
        self.closer_username = username
        self.closer_at = now_iso()
        if not preserve_status and self.status != "closed":
            self.status = "in_progress"
        return self

    def get_template_for_phase(self, phase: str) -> str:
        if self.template_phase_map:
            template_id = self.template_phase_map.get(phase)
            if template_id:
                return template_id
        if phase == "open":
            return self.template_open_id
        if phase == "continue":
            # For "continue" phase, fall back to phase_map or use closing template
            return self.template_phase_map.get("continue", self.template_close_id)
        if phase == "close":
            return self.template_close_id
        return ""


def _parse_phase_map(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str) and value:
            cleaned[key] = value
    return cleaned
