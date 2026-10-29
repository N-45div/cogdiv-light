"""Compare ways of correcting the wind forecast on days it has not seen (docs/DECISIONS.md, C6).

Usage:
    python backend/scripts/compare_corrections.py           # January 2026 snapshot
    python backend/scripts/compare_corrections.py --live    # the last 30 days from Elexon

Learns from every forecast run's errors before the last `--test-days` days, then scores each
method on those last days: average error of the expected value, and how often the actual
fell inside the 80% range (it should be about 80%).
    raw     the forecast as published
    offset  forecast minus the median past error (a fixed shift per lead time)
    ratio   forecast times the median past actual/forecast ratio (a scale per lead time)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor import analysis as an  # noqa: E402
from wind_monitor import outlook as ol  # noqa: E402
from wind_monitor.config import Settings  # noqa: E402
from wind_monitor.elexon import ElexonClient  # noqa: E402
from wind_monitor.store import LiveWindow, load_snapshot  # noqa: E402


def compare(data, end: pd.Timestamp, test_days: int) -> pd.DataFrame:
    pairs = ol.error_history(data, end, days=60)
    cut = end - pd.Timedelta(days=test_days)
    train, test = pairs[pairs["start_time"] < cut], pairs[pairs["start_time"] >= cut]
    keys, forecast, actual = test["lead_bucket"], test["forecast_mw"], test["actual_mw"]

    e_low, e_mid, e_high = an.group_quantiles(keys, train, "lead_bucket", "error_mw")
    r_low, r_mid, r_high = an.group_quantiles(
        keys, train.dropna(subset=["ratio"]), "lead_bucket", "ratio"
    )
    methods = {
        "raw": (forecast, None, None),
        "offset": (
            (forecast - e_mid).clip(lower=0),
            (forecast - e_high).clip(lower=0),
            (forecast - e_low).clip(lower=0),
        ),
        "ratio": (forecast * r_mid, forecast * r_low, forecast * r_high),
    }
    rows = {}
    for name, (mid, low, high) in methods.items():
        row = {"average error (MW)": round((mid - actual).abs().mean())}
        if low is not None:
            row["80% range held the actual"] = f"{((actual >= low) & (actual <= high)).mean():.0%}"
            row["range width (MW)"] = round((high - low).mean())
        rows[name] = row
    print(f"Learned from {len(train):,} forecast/actual pairs; tested on {len(test):,}.")
    return pd.DataFrame(rows).T.fillna("")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="use the last 30 days from Elexon")
    parser.add_argument("--test-days", type=int, default=7)
    args = parser.parse_args()
    if args.live:
        end = pd.Timestamp.now(tz="UTC").floor("min")
        data = LiveWindow(ElexonClient(Settings.from_env().elexon_base_url)).get(end)
        label = f"last 30 days to {end:%d %b %Y %H:%M} UTC"
    else:
        data = load_snapshot(Settings.from_env().snapshot_dir)
        end, label = pd.Timestamp("2026-02-02T00:00Z"), "January 2026 snapshot"
    print(f"## {label}: tested on the last {args.test_days} days\n")
    print(compare(data, end, args.test_days).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
