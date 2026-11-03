"""Small synthetic wind datasets with known answers, shared by the agent tests."""

from datetime import UTC, datetime

import pandas as pd

from wind_monitor.store import WindData

FIRST = pd.Timestamp("2026-01-01T00:00Z")
# 18:00 UK on Thu 8 Jan (GMT, so UK time = UTC).
NOW = pd.Timestamp("2026-01-08T18:00Z")


def actual_mw(moment: pd.Timestamp) -> float:
    """10,000 MW, except 02:00-04:00 every night, when it is 15,000 MW."""
    return 15_000.0 if moment.hour in (2, 3) else 10_000.0


def synthetic_wind(near: float = 1.02, far: float = 1.2, days: int = 12) -> WindData:
    """A run every 6 hours for the next 48 hours: `near` x actual when under 6 h ahead, `far` x
    actual beyond. Scaling by each lead time's track record recovers the actual exactly."""
    halves = pd.date_range(FIRST, periods=48 * days, freq="30min")
    actuals = pd.DataFrame({"start_time": halves, "generation_mw": [actual_mw(t) for t in halves]})
    rows = []
    for published in pd.date_range(FIRST - pd.Timedelta(days=1), periods=4 * (days + 1), freq="6h"):
        for h in range(1, 49):
            target = published + pd.Timedelta(hours=h)
            rows.append((published, target, actual_mw(target) * (near if h < 6 else far)))
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


def night_wind(early, late, actual, days: int = 8) -> WindData:
    """Runs every 2 hours for 48 hours. A run published before 20:00 the evening before a target
    uses early(target); from 20:00 on, late(target). Actual wind is actual(target)."""
    halves = pd.date_range(FIRST, periods=48 * days, freq="30min")
    actuals = pd.DataFrame({"start_time": halves, "generation_mw": [actual(t) for t in halves]})
    rows = []
    runs = pd.date_range(FIRST - pd.Timedelta(days=2), periods=12 * (days + 2), freq="2h")
    for published in runs:
        for h in range(1, 49):
            target = published + pd.Timedelta(hours=h)
            evening = (target - pd.Timedelta(hours=12)).normalize() + pd.Timedelta(hours=20)
            value = early(target) if published < evening else late(target)
            rows.append((published, target, value))
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


def carbon_g(region: int, moment: pd.Timestamp) -> float:
    """Synthetic g CO2/kWh by region (UTC hours; UK time in January):
    GB 200, but 100 at 02:00-04:00 (the windy hours) and 60 at 04:00-05:00 (low demand);
    North Scotland 10, and 0 from 01:00 to 06:00; London 220, but 150 at 03:00-04:00;
    every other region 100 + 10 x its id."""
    h = moment.hour
    if region == 18:
        return 100.0 if h in (2, 3) else 60.0 if h == 4 else 200.0
    if region == 1:
        return 0.0 if 1 <= h < 6 else 10.0
    if region == 13:
        return 150.0 if h == 3 else 220.0
    return 100.0 + 10 * region


def carbon_periods(start: pd.Timestamp = NOW, hours: int = 48, g=None) -> list[dict]:
    """What the Carbon Intensity API's /regional/intensity/{from}/fw48h returns: it starts with
    the half-hour before `start`, like the real API on a half-hour boundary."""
    first = start.floor("30min") - pd.Timedelta(minutes=30)
    periods = []
    for i in range(hours * 2 + 1):
        t = first + pd.Timedelta(minutes=30 * i)
        regions = [
            {
                "regionid": rid,
                "shortname": f"region {rid}",
                "intensity": {"forecast": (g or carbon_g)(rid, t), "index": "moderate"},
                "generationmix": [
                    {"fuel": "wind", "perc": 90.0 if rid == 1 else 20.0 if rid == 13 else 40.0},
                    {"fuel": "gas", "perc": 5.0 if rid == 1 else 50.0},
                ],
            }
            for rid in range(1, 19)
        ]
        end = t + pd.Timedelta(minutes=30)
        periods.append(
            {"from": f"{t:%Y-%m-%dT%H:%MZ}", "to": f"{end:%Y-%m-%dT%H:%MZ}", "regions": regions}
        )
    return periods


def synthetic_carbon(start: pd.Timestamp = NOW, g=None):
    """The synthetic forecast; `g(region, moment)` replaces carbon_g for a changed forecast."""
    from wind_monitor.carbon import CarbonForecast, forecast_frames

    regions, national = forecast_frames(carbon_periods(start, g=g))
    return CarbonForecast(regions, national, start, start.floor("30min"))
