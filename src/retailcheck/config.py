from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class GoogleConfig:
    sheets_id: str
    service_account_json: Path


@dataclass(frozen=True)
class BotConfig:
    token: str


@dataclass(frozen=True)
class RedisConfig:
    url: str


@dataclass(frozen=True)
class TemplateDefaults:
    phase_map: Mapping[str, str]

    def get(self, phase: str) -> str:
        return self.phase_map.get(phase, "")

    @property
    def opening_template_id(self) -> str:
        return self.get("open")

    @property
    def closing_template_id(self) -> str:
        return self.get("close")


@dataclass(frozen=True)
class RunSettings:
    lock_ttl_sec: int
    template_defaults: TemplateDefaults
    scope: str


@dataclass(frozen=True)
class AlertSettings:
    delta_threshold_rub: float
    delta_cooldown_sec: int


@dataclass(frozen=True)
class AppConfig:
    bot: BotConfig
    redis: RedisConfig
    google: GoogleConfig
    run: RunSettings
    notifications: NotificationsConfig
    alerts: AlertSettings


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def get_google_config() -> GoogleConfig:
    sheets_id = _require_env("GOOGLE_SHEETS_ID")
    service_account = _require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    service_path = Path(service_account).expanduser()
    if not service_path.exists():
        raise FileNotFoundError(f"Service account JSON not found: {service_path}")
    return GoogleConfig(sheets_id=sheets_id, service_account_json=service_path)


def load_app_config() -> AppConfig:
    google = get_google_config()
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    lock_ttl = int(os.getenv("REDIS_RUN_LOCK_TTL_SEC", "10"))
    run_scope = os.getenv("RUN_SCOPE", "shop_id_date")
    opening_template = os.getenv(
        "DEFAULT_TEMPLATE_OPEN_ID",
        os.getenv("DEFAULT_TEMPLATE_ID", "opening_v1"),
    )
    closing_template = os.getenv("DEFAULT_TEMPLATE_CLOSE_ID", "closing_v1")
    check_1100_template = os.getenv("DEFAULT_TEMPLATE_CHECK_1100_ID", closing_template)
    check_1600_template = os.getenv("DEFAULT_TEMPLATE_CHECK_1600_ID", check_1100_template)
    check_1900_template = os.getenv("DEFAULT_TEMPLATE_CHECK_1900_ID", check_1600_template)
    finance_template = os.getenv("DEFAULT_TEMPLATE_FINANCE_ID", closing_template)
    phase_map = MappingProxyType(
        {
            "open": opening_template,
            "check_1100": check_1100_template,
            "check_1600": check_1600_template,
            "check_1900": check_1900_template,
            "close": closing_template,
            "finance": finance_template,
        }
    )

    notifications = NotificationsConfig(
        manager_chat_ids=_parse_chat_ids(os.getenv("MANAGER_NOTIFY_CHAT_IDS", "")),
    )
    alerts = AlertSettings(
        delta_threshold_rub=float(os.getenv("DELTA_THRESHOLD_RUB", "300")),
        delta_cooldown_sec=int(os.getenv("DELTA_ALERT_COOLDOWN_SEC", "3600")),
    )

    return AppConfig(
        bot=BotConfig(token=bot_token),
        redis=RedisConfig(url=redis_url),
        google=google,
        run=RunSettings(
            lock_ttl_sec=lock_ttl,
            template_defaults=TemplateDefaults(phase_map=phase_map),
            scope=run_scope,
        ),
        notifications=notifications,
        alerts=alerts,
    )


@dataclass(frozen=True)
class NotificationsConfig:
    manager_chat_ids: list[int]


def _parse_chat_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as err:
            raise RuntimeError(f"Invalid chat id in MANAGER_NOTIFY_CHAT_IDS: {part}") from err
    return ids
