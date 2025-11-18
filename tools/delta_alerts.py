"""
Check delta alerts and notify managers if threshold is exceeded.

Usage:
    poetry run python tools/delta_alerts.py
"""

from __future__ import annotations

import asyncio

from retailcheck.alerts.delta import run_delta_alerts


def main() -> None:
    asyncio.run(run_delta_alerts())


if __name__ == "__main__":
    main()
