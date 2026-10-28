"""What could be known at a moment T, with no look-ahead.

- A forecast run counts once it is published (publish_time <= T).
- An actual counts once its half-hour has ended and Elexon has had time to publish it.
- The likely range of the actual comes from the errors of EVERY forecast run over the past
  `HISTORY_DAYS`, grouped by how far ahead the forecast was made. A forecast made 3 hours ahead
  gets the error profile of 3-hour-ahead forecasts, not of day-ahead ones. This is what lets a
  re-plan on a fresher forecast use a tighter range.

The same functions serve live mode (T = now), the replay demo and the backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import analysis as an
from .config import HISTORY_DAYS, MAX_LEAD_HOURS
from .store import WindData

# Assumption: a half-hour's actual is published within 30 minutes of the half-hour ending.
# Elexon's FUELHH data is near real time; this keeps the replay from seeing actuals early.
ACTUALS_PUBLISH_LAG = pd.Timedelta(minutes=30)

# Forecasts made this many hours ahead share an error profile.
LEAD_BUCKET_EDGES = (0, 6, 12, 24)
LEAD_BUCKET_LABELS = ("0-6h", "6-12h", "12-24h", "24h+")


def known_actuals(actuals: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Actuals already published at `as_of`."""
    published = actuals["start_time"] + an.HALF_HOUR + ACTUALS_PUBLISH_LAG <= as_of
    return actuals[published]


def published_forecasts(forecasts: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    return forecasts[forecasts["publish_time"] <= as_of]


def lead_bucket(lead_hours: pd.Series) -> pd.Series:
    """Label for how far ahead each forecast was made (negative leads count as 0-6h)."""
    bins = [*LEAD_BUCKET_EDGES, np.inf]
    labels = pd.cut(lead_hours.clip(lower=0), bins=bins, right=False, labels=LEAD_BUCKET_LABELS)
    return labels.astype(object)


def error_history(data: WindData, as_of: pd.Timestamp, days: float = HISTORY_DAYS) -> pd.DataFrame:
    """Errors of every run for target times in the `days` before `as_of`, known by `as_of`.

    Columns: as `analysis.run_errors`, plus lead_bucket.
    """
    pairs = an.run_errors(
        published_forecasts(data.forecasts, as_of),
        known_actuals(data.actuals, as_of),
        as_of - pd.Timedelta(days=days),
        as_of,
        MAX_LEAD_HOURS,
    )
    pairs["lead_bucket"] = lead_bucket(pairs["lead_hours"])
    return pairs


def outlook(
    data: WindData,
    as_of: pd.Timestamp,
    hours: int = 48,
    history_days: float = HISTORY_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The forecast at `as_of` for the next `hours` (from the start of the current hour).

    Returns (forecast, history). Forecast columns: start_time, forecast_mw, publish_time,
    lead_hours, lead_bucket, expected_mw, low_mw, high_mw. Hours past the newest run's horizon
    are simply absent.
    """
    start = as_of.floor("h")
    forecast = an.forecast_as_of(data.forecasts, as_of, start, start + pd.Timedelta(hours=hours))
    forecast["lead_hours"] = (forecast["start_time"] - forecast["publish_time"]) / an.HOUR
    forecast["lead_bucket"] = lead_bucket(forecast["lead_hours"])
    history = error_history(data, as_of, history_days)
    banded = an.apply_error_band(forecast, forecast["lead_bucket"], history, "lead_bucket")
    return banded, history


def history_summary(history: pd.DataFrame) -> list[dict]:
    """Bias and spread of past errors for each lead-time bucket (for the agent and the UI)."""
    rows = []
    for label in LEAD_BUCKET_LABELS:
        err = history.loc[history["lead_bucket"] == label, "error_mw"]
        rows.append(
            {
                "lead_bucket": label,
                "n": int(len(err)),
                "bias_mw": an._num(err.mean()) if len(err) else None,
                "mae_mw": an._num(err.abs().mean()) if len(err) else None,
                "p10_error_mw": an._num(err.quantile(0.1)) if len(err) else None,
                "p90_error_mw": an._num(err.quantile(0.9)) if len(err) else None,
                "over_forecast_share_pct": an._num((err > 0).mean() * 100) if len(err) else None,
            }
        )
    return rows


def recent_tracking(data: WindData, as_of: pd.Timestamp, hours: float = 6) -> pd.DataFrame:
    """Actual wind over the last `hours`, against the freshest forecast made before each hour.

    This answers "is wind coming in below forecast right now?". Columns: start_time,
    actual_mw, forecast_mw, error_mw (forecast - actual), lead_hours, publish_time.
    """
    pairs = an.run_errors(
        published_forecasts(data.forecasts, as_of),
        known_actuals(data.actuals, as_of),
        as_of - pd.Timedelta(hours=hours),
        as_of,
        MAX_LEAD_HOURS,
    )
    freshest = pairs.sort_values("lead_hours").drop_duplicates("start_time", keep="first")
    columns = ["start_time", "actual_mw", "forecast_mw", "error_mw", "lead_hours", "publish_time"]
    return freshest.sort_values("start_time")[columns].reset_index(drop=True)


def latest_actual(data: WindData, as_of: pd.Timestamp) -> dict | None:
    """The newest published half-hour of actual wind."""
    known = known_actuals(data.actuals, as_of)
    if known.empty:
        return None
    row = known.sort_values("start_time").iloc[-1]
    return {"time": row["start_time"].to_pydatetime(), "actual_mw": an._num(row["generation_mw"])}
