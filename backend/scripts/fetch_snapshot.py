"""Download the January 2026 wind data from Elexon and save it as the app's snapshot.

Usage (from the backend folder):  python scripts/fetch_snapshot.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor import config  # noqa: E402
from wind_monitor.store import fetch_live, save_snapshot  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = config.Settings.from_env()
    data = fetch_live(settings)
    save_snapshot(data, settings.snapshot_dir)

    a, f = data.actuals, data.forecasts
    print(f"Saved snapshot to {settings.snapshot_dir}")
    print(f"  actuals:   {len(a)} rows, {a.start_time.min()} -> {a.start_time.max()}")
    print(f"  forecasts: {len(f)} rows, {f.publish_time.nunique()} runs")
    print(f"             published {f.publish_time.min()} -> {f.publish_time.max()}")


if __name__ == "__main__":
    main()
