"""Send reminder messages for pending steps (interval-based).

Usage:
    poetry run python tools/send_reminders.py --mode pending_steps [--shop-id shop_1]
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from retailcheck.reminders.service import run_reminders


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send RetailCheck reminders.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("pending_steps",),
        help="Тип напоминания: pending_steps — пока не закрыты обязательные шаги.",
    )
    parser.add_argument(
        "--shop-id",
        action="append",
        help="ID магазина. Можно указать несколько флагов. Без параметра — все магазины.",
    )
    return parser.parse_args()


def _normalize_shops(values: Sequence[str] | None) -> list[str] | None:
    if not values:
        return None
    shops: list[str] = []
    for value in values:
        parts = [token.strip() for token in value.split(",") if token.strip()]
        shops.extend(parts)
    return shops or None


def main() -> None:
    args = parse_args()
    shops = _normalize_shops(args.shop_id)
    asyncio.run(run_reminders(args.mode, shops))


if __name__ == "__main__":
    main()
