"""Backtest the charging strategies night by night (see wind_monitor/backtest.py).

Usage:
    python backend/scripts/backtest.py                 # January 2026 snapshot
    python backend/scripts/backtest.py --live          # the last 30 days from Elexon
    python backend/scripts/backtest.py --kwh 7 --kw 7 --plug-in 21:00 --deadline 07:00

Prints the average actual wind while charging for each strategy, and how much of the gain
that perfect hindsight could get each one caught.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, time, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor import backtest as bt  # noqa: E402
from wind_monitor.config import HISTORY_DAYS, Settings  # noqa: E402
from wind_monitor.elexon import ElexonClient  # noqa: E402
from wind_monitor.store import LiveWindow, load_snapshot  # noqa: E402

# Nights need a few days of past errors before them for the corrected forecast.
WARM_UP_DAYS = 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="the last 30 days from Elexon")
    parser.add_argument("--kwh", type=float, default=21.0)
    parser.add_argument("--kw", type=float, default=7.0)
    parser.add_argument("--plug-in", default="18:00")
    parser.add_argument("--deadline", default="07:00")
    parser.add_argument("--timer", default="00:00", help="start time of the fixed timer")
    parser.add_argument("--nights", action="store_true", help="also print every night")
    args = parser.parse_args()

    if args.live:
        now = pd.Timestamp.now(tz="UTC")
        data = LiveWindow(ElexonClient(Settings.from_env().elexon_base_url)).get(now)
        last = (now - pd.Timedelta(days=1)).date() - timedelta(days=1)  # last complete night
        first = data.actuals["start_time"].min().date() + timedelta(days=WARM_UP_DAYS)
        label = "Live data (Elexon)"
    else:
        data = load_snapshot(Settings.from_env().snapshot_dir)
        first, last = date(2026, 1, 1) + timedelta(days=WARM_UP_DAYS), date(2026, 1, 31)
        label = "January 2026 snapshot"
    setup = bt.Night(
        args.kwh,
        args.kw,
        time.fromisoformat(args.plug_in),
        time.fromisoformat(args.deadline),
        time.fromisoformat(args.timer),
    )
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    nights = bt.run(data, days, setup)
    print(
        f"## {label}: {len(days)} nights from {first:%d %b} to {last:%d %b}; "
        f"{args.kwh:g} kWh at {args.kw:g} kW, plug in {args.plug_in}, done by {args.deadline} "
        f"(UK time); timer at {args.timer}; error history {HISTORY_DAYS} days\n"
    )
    print(bt.summary(nights).to_string())
    if args.nights:
        print("\n" + nights.round(0).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
