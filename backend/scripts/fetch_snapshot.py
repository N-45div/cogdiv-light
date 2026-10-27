"""Download the January 2026 wind data from Elexon and save it as the app's snapshot.

Usage (from the repo root):  python backend/scripts/fetch_snapshot.py

Also prints a short profile of the data (publishing times, resolution, horizon), so the
assumptions the analysis relies on can be checked against what Elexon actually returns.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor import analysis, config  # noqa: E402
from wind_monitor.store import WindData, fetch_live, save_snapshot  # noqa: E402

HOUR = pd.Timedelta(hours=1)


def profile(data: WindData) -> None:
    a, f = data.actuals, data.forecasts
    print("\n== Actuals (FUELHH, fuelType=WIND) ==")
    print(f"rows {len(a)}, {a.start_time.min()} -> {a.start_time.max()}")
    steps = a.start_time.diff().dropna().value_counts().head(3)
    print("step between rows:", {str(k): int(v) for k, v in steps.items()})
    print(f"generation MW: min {a.generation_mw.min():.0f}, mean {a.generation_mw.mean():.0f}, "
          f"max {a.generation_mw.max():.0f}")  # fmt: skip

    print("\n== Forecasts (WINDFOR) ==")
    print(f"rows {len(f)}, runs {f.publish_time.nunique()}, "
          f"published {f.publish_time.min()} -> {f.publish_time.max()}")  # fmt: skip
    runs = f.groupby("publish_time")
    per_run = pd.DataFrame(
        {
            "points": runs.size(),
            "step": runs.start_time.apply(lambda s: s.diff().median()),
            "first_lead_h": runs.apply(lambda g: (g.start_time.min() - g.name) / HOUR),
            "last_lead_h": runs.apply(lambda g: (g.start_time.max() - g.name) / HOUR),
        }
    )
    print("points per run:", per_run.points.describe()[["min", "50%", "max"]].to_dict())
    print("step within run:", per_run.step.value_counts().head(3).to_dict())
    print(
        "lead of first point (h):",
        per_run.first_lead_h.describe()[["min", "50%", "max"]].round(1).to_dict(),
    )
    print(
        "lead of last point (h):",
        per_run.last_lead_h.describe()[["min", "50%", "max"]].round(1).to_dict(),
    )
    times_of_day = f.publish_time.drop_duplicates().dt.strftime("%H:%M").value_counts().sort_index()
    print("publish times of day (UTC) and how often:", times_of_day.to_dict())
    print("generation MW: min", round(f.generation_mw.min()), "max", round(f.generation_mw.max()))

    print("\n== Decision forecasts at the default cut-off ==")
    days = analysis.date_range(config.DELIVERY_FIRST, config.DELIVERY_LAST)
    errors = analysis.decision_errors(f, a, days, config.DEFAULT_CUTOFF)
    used = errors.groupby("delivery_date").agg(
        run=("publish_time", "max"), points=("start_time", "size"),
        lead_min=("lead_hours", "min"), lead_max=("lead_hours", "max"),
    )  # fmt: skip
    print(f"delivery days with a decision forecast: {len(used)} of {len(days)}")
    print("hourly points matched per day:", used.points.value_counts().to_dict())
    print(
        "decision run publish time of day:", used.run.dt.strftime("%H:%M").value_counts().to_dict()
    )
    print(f"lead hours range: {used.lead_min.min():.1f} -> {used.lead_max.max():.1f}")
    summary = analysis.error_summary(errors.error_mw, errors.actual_mw)
    print(
        "error summary:",
        {k: round(v, 1) if isinstance(v, float) else v for k, v in summary.items()},
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = config.Settings.from_env()
    data = fetch_live(settings)
    save_snapshot(data, settings.snapshot_dir)
    print(f"Saved snapshot to {settings.snapshot_dir}")
    profile(data)


if __name__ == "__main__":
    main()
