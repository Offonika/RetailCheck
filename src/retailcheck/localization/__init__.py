from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_LOCALE = "ru"


def _candidate_locale_dirs() -> list[Path]:
    candidates = [PACKAGE_DIR]
    for parent in PACKAGE_DIR.parents:
        candidates.append(parent / "locale")
    # Remove duplicates while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


@lru_cache(maxsize=1)
def _load_locale(locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    for base in _candidate_locale_dirs():
        path = base / locale / "messages.json"
        if path.exists():
            with path.open(encoding="utf-8") as fp:
                return json.load(fp)
    return {}


def gettext(key: str, **kwargs: Any) -> str:
    data = _load_locale()
    template: Any = data
    for part in key.split("."):
        if isinstance(template, dict):
            template = template.get(part)
        else:
            template = None
            break
    if template is None:
        template = key
    try:
        return str(template).format(**kwargs)
    except Exception:
        return str(template)
