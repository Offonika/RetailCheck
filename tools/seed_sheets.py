"""
Utility to bootstrap Google Sheets with the CSV data stored in `data/sheets`.

Environment variables `GOOGLE_SHEETS_ID` и `GOOGLE_SERVICE_ACCOUNT_JSON`
используются для подключения к Google Sheets. Опционально можно указать
другой каталог с CSV через `--data-dir`.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from retailcheck.config import get_google_config
from retailcheck.sheets.client import SheetsClient

DATA_DIR = Path("data/sheets")

SHEET_TO_FILE: dict[str, str] = {
    "Users": "Users.csv",
    "Shops": "Shops.csv",
    "Templates": "Templates.csv",
    "TemplateSteps": "TemplateSteps.csv",
    "Runs": "Runs.csv",
    "RunSteps": "RunSteps.csv",
    "Attachments": "Attachments.csv",
    "Audit": "Audit.csv",
    "Export": "Export.csv",
}


def load_csv(path: Path) -> list[list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp)
        return [row for row in reader]


def seed_sheet(client: SheetsClient, sheet_name: str, values: list[list[str]]) -> None:
    if not values:
        raise ValueError(f"No values to write for sheet {sheet_name}")
    client.clear(sheet_name)
    client.write(f"{sheet_name}!A1", values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Google Sheets with CSV data.")
    parser.add_argument(
        "--data-dir",
        default=DATA_DIR,
        type=Path,
        help="Directory with CSV files (default: data/sheets).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    google_cfg = get_google_config()
    client = SheetsClient(google_cfg.sheets_id, google_cfg.service_account_json)
    for sheet_name, filename in SHEET_TO_FILE.items():
        csv_path = args.data_dir / filename
        values = load_csv(csv_path)
        seed_sheet(client, sheet_name, values)
        print(f"[OK] Seeded {sheet_name} from {csv_path}")


if __name__ == "__main__":
    main()
