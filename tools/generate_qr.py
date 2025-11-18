"""
Generate QR codes for shop open/close deep links.

Usage:
    poetry run python tools/generate_qr.py --bot-username Retailcheck_bot --output ops/qr
"""

from __future__ import annotations

import argparse
from pathlib import Path

import qrcode

from retailcheck.config import get_google_config
from retailcheck.sheets.client import SheetsClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate QR codes for shop payloads.")
    parser.add_argument(
        "--bot-username",
        required=True,
        help="Telegram bot username (without @).",
    )
    parser.add_argument(
        "--output",
        default="ops/qr",
        help="Directory to store generated PNG files.",
    )
    return parser.parse_args()


def load_shops(client: SheetsClient) -> list[tuple[str, str]]:
    rows = client.read("Shops!A2:B")
    shops = []
    for row in rows:
        if row and row[0]:
            shops.append((row[0], row[1] if len(row) > 1 else row[0]))
    return shops


def main() -> None:
    args = parse_args()
    cfg = get_google_config()
    client = SheetsClient(cfg.sheets_id, cfg.service_account_json)
    shops = load_shops(client)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    for shop_id, name in shops:
        for role in ("open", "close"):
            payload = f"https://t.me/{args.bot_username}?start={shop_id}__{role}"
            img = qrcode.make(payload)
            file_path = output_dir / f"{shop_id}_{role}.png"
            img.save(str(file_path))
            print(f"[OK] {name} ({shop_id}) {role} → {file_path}")


if __name__ == "__main__":
    main()
