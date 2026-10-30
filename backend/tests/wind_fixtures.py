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
