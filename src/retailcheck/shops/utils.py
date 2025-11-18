from __future__ import annotations

import json
from collections.abc import Iterable


def _parse_usernames(raw: str) -> list[str]:
    cleaned = raw.replace(",", " ")
    values = [token.strip().lstrip("@") for token in cleaned.split() if token.strip()]
    return [token for token in values if token]


def _parse_slots(raw: str) -> dict[str, list[str]]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return {}
    if cleaned.startswith("{") or cleaned.startswith("["):
        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                return {str(key): _normalize_slot_list(value) for key, value in payload.items()}
            if isinstance(payload, list):
                return {"custom": _normalize_slot_list(payload)}
        except json.JSONDecodeError:
            pass
    slots = [part.strip() for part in cleaned.split(",") if part.strip()]
    return {"custom": slots} if slots else {}


def _normalize_slot_list(value: Iterable | str) -> list[str]:
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    normalized = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_time(raw: str, fallback: str) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        return fallback
    try:
        hour, minute = candidate.split(":", 1)
        hour_i = max(0, min(23, int(hour)))
        minute_i = max(0, min(59, int(minute)))
        return f"{hour_i:02d}:{minute_i:02d}"
    except ValueError:
        return fallback
