"""End-to-end check of the planning API in live mode, against real Elexon data.

Usage (API already running with CLOCK_MODE=live):
    python backend/scripts/live_check.py --base-url http://localhost:8000

Checks the clock is live, the outlook has fresh forecasts and actuals, and a plan comes back.
Also measures how soon Elexon publishes each half-hour of actual wind, to test the 30-minute
assumption in docs/DECISIONS.md (C1). (Elexon's publishTime is just the end of the half-hour,
so the delay is measured from the newest half-hour available right now instead.)
Prints Markdown (for the GitHub job summary) and exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor.config import Settings  # noqa: E402
from wind_monitor.elexon import ElexonClient  # noqa: E402

# Forecast runs come every 2-4 hours; actuals within about an hour.
MAX_RUN_AGE = timedelta(hours=6)
MAX_ACTUAL_AGE = timedelta(hours=3)


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.failed += not ok
        print(f"- {'✅' if ok else '❌'} {name}{f': {detail}' if detail else ''}")


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _mw(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f} MW"


def check_api(base_url: str, checks: Checks) -> None:
    api = httpx.Client(base_url=base_url, timeout=180)
    now = datetime.now(UTC)

    print("## Planning API, live mode\n")
    checks.check("health", api.get("/api/health").status_code == 200)

    clock = api.get("/api/clock").json()
    drift = abs(_time(clock["now"]) - now)
    checks.check("clock is live", clock["mode"] == "live", f"mode={clock['mode']}")
    checks.check("clock is real time", drift < timedelta(minutes=2), f"off by {drift}")

    response = api.get("/api/outlook", params={"hours": 24, "recent_hours": 6})
    checks.check("outlook answers", response.status_code == 200, f"HTTP {response.status_code}")
    if response.status_code != 200:
        print(f"\n```\n{response.text[:500]}\n```")
        return
    outlook = response.json()
    run_age = now - _time(outlook["latest_run"]) if outlook["latest_run"] else None
    checks.check(
        "newest forecast run is recent",
        run_age is not None and run_age < MAX_RUN_AGE,
        f"published {outlook['latest_run']} ({run_age} ago)",
    )
    actual = outlook["latest_actual"]
    actual_age = now - _time(actual["time"]) if actual else None
    checks.check(
        "newest actual is recent",
        actual_age is not None and actual_age < MAX_ACTUAL_AGE,
        f"{actual['time']} = {_mw(actual['actual_mw'])}" if actual else "none",
    )
    points = outlook["points"]
    banded = sum(p["expected_mw"] is not None for p in points)
    checks.check("outlook covers the next day", len(points) >= 20, f"{len(points)} hours")
    checks.check("bias correction available", banded == len(points), f"{banded}/{len(points)}")
    checks.check("error history", outlook["history"]["n"] > 1000, f"{outlook['history']['n']:,}")

    print("\n| Lead time | Pairs | Bias | MAE | Forecast too high | Metered / forecast |")
    print("|---|---|---|---|---|---|")
    for row in outlook["history"]["by_lead"]:
        high, share = row["over_forecast_share_pct"], row["actual_share_pct"]
        print(
            f"| {row['lead_bucket']} | {row['n']:,} | {_mw(row['bias_mw'])} | "
            f"{_mw(row['mae_mw'])} | {'n/a' if high is None else f'{high:.0f}%'} | "
            f"{'n/a' if share is None else f'{share:.0f}%'} |"
        )
    print("\n| Hour (UTC) | Forecast | Expected | Range | Made ahead |")
    print("|---|---|---|---|---|")
    for p in points[:8]:
        print(
            f"| {p['time'][5:16]} | {_mw(p['forecast_mw'])} | {_mw(p['expected_mw'])} | "
            f"{_mw(p['low_mw'])} - {_mw(p['high_mw'])} | {p['lead_hours']:.1f} h |"
        )
    recent = outlook["recent"]
    print(f"\nLast {recent['hours']:g} h: forecast - actual = {_mw(recent['mean_error_mw'])}\n")

    deadline = (now + timedelta(hours=12)).isoformat()
    response = api.post("/api/plan", json={"energy_kwh": 14, "power_kw": 7, "deadline": deadline})
    checks.check("plan answers", response.status_code == 200, f"HTTP {response.status_code}")
    if response.status_code == 200:
        plan = response.json()
        runs = ", ".join(f"{b['start'][5:16]} to {b['end'][11:16]}" for b in plan["blocks"])
        checks.check("plan is feasible", plan["feasible"], f"14 kWh at 7 kW: {runs}")
        wind = plan["wind"]
        print(
            f"\nExpected wind while charging: {_mw(wind['planned']['expected_mw'])} "
            f"(starting straight away: {_mw(wind['asap']['expected_mw'])}). "
            f"Notes: {plan['notes'] or 'none'}\n"
        )

    study = api.get("/api/accuracy", params={"cutoff": "09:20"})
    checks.check("January views still served", study.status_code == 200)


def check_publication_lag(checks: Checks) -> None:
    """How soon after a half-hour ends is its actual wind available? (DECISIONS C1)"""
    print("\n## Elexon publication timing\n")
    client = ElexonClient(Settings.from_env().elexon_base_url)
    now = datetime.now(UTC)
    rows = client.fetch_wind_actuals(now.date() - timedelta(days=1), now.date())
    if not rows:
        checks.check("FUELHH rows for the last two days", False, "none returned")
        return
    newest = max(pd.Timestamp(r["startTime"]) for r in rows)
    ended = newest + pd.Timedelta(minutes=30)
    delay = now - ended.to_pydatetime()
    print(
        f"Newest half-hour available: {newest:%H:%M}-{ended:%H:%M} UTC, which ended "
        f"{delay.total_seconds() / 60:.0f} min ago, so Elexon published it within that time.\n"
    )
    # The newest half-hour that has ended must be there within 30 min (+ 30 min slack for a
    # half-hour that is still running).
    checks.check("actuals arrive within the assumed 30 minutes", delay <= timedelta(minutes=60))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    checks = Checks()
    check_api(args.base_url, checks)
    check_publication_lag(checks)
    verdict = f"{checks.failed} check(s) failed" if checks.failed else "All checks passed"
    print(f"\n**{verdict}**")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
