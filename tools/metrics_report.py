"""
Generate simple metrics for RetailCheck runs.

Usage:
    poetry run python tools/metrics_report.py
"""

from __future__ import annotations

import asyncio
from collections import Counter

from retailcheck.config import load_app_config
from retailcheck.runs.repository import RunsRepository
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.sheets.client import SheetsClient


async def main() -> None:
    config = load_app_config()
    sheets = SheetsClient(
        spreadsheet_id=config.google.sheets_id,
        service_account_file=config.google.service_account_json,
    )
    runs_repo = RunsRepository(sheets)
    runsteps_repo = RunStepsRepository(sheets)

    runs = await runs_repo.list_runs()
    statuses = Counter(run.status for run in runs)
    closer_assigned = len([run for run in runs if run.closer_user_id])
    delta_values = []
    for run in runs:
        if run.delta_rub:
            try:
                delta_values.append(float(run.delta_rub))
            except ValueError:
                continue
        else:
            steps = await runsteps_repo.list_for_run(run.run_id)
            total = sum(float(step.delta_number) for step in steps if step.delta_number)
            if total:
                delta_values.append(total)

    print("=== RetailCheck Metrics ===")
    print("Всего смен:", len(runs))
    for status, count in statuses.items():
        print(f"- {status}: {count}")
    if runs:
        print(f"Коэффициент закрытия (closer назначен): {closer_assigned / len(runs):.2%}")
    if delta_values:
        avg_delta = sum(delta_values) / len(delta_values)
        print(f"Средняя дельта: {avg_delta:+.2f} ₽")
        max_delta = max(delta_values, key=abs)
        print(f"Максимальная дельта по модулю: {max_delta:+.2f} ₽")


if __name__ == "__main__":
    asyncio.run(main())
