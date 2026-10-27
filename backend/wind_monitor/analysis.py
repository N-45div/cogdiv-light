"""Core analysis, written as plain functions over pandas tables so it is easy to unit-test.

Sign convention used everywhere:
    error = forecast - actual
    error > 0  -> over-forecast: less wind came than expected, so the desk is SHORT
                  and must buy the gap later (intraday / imbalance), usually at a worse price.
    error < 0  -> under-forecast: more wind than expected, the desk is LONG.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .config import MAX_LEAD_HOURS, UK_TZ

HOUR = pd.Timedelta(hours=1)
# Band shown around the decision forecast: 10th-90th percentile of past errors (80% range).
BAND_LOW_Q = 0.10
BAND_HIGH_Q = 0.90


# ---- time helpers ---------------------------------------------------------------------


def uk_moment(day: date, at: time) -> pd.Timestamp:
    """A UK local wall-clock time on a given day, as a UTC timestamp."""
    return pd.Timestamp(datetime.combine(day, at, UK_TZ)).tz_convert("UTC")


def delivery_window(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """GB delivery day = UK local midnight to midnight (48 half-hours, 46/50 on clock change)."""
    return uk_moment(day, time(0)), uk_moment(day + timedelta(days=1), time(0))


def decision_cutoff(delivery_day: date, cutoff: time) -> pd.Timestamp:
    """The moment the day-ahead decision is made: the cutoff time on the day before delivery."""
    return uk_moment(delivery_day - timedelta(days=1), cutoff)


def date_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# ---- which forecast did the desk have? -------------------------------------------------


def decision_forecast(forecasts: pd.DataFrame, delivery_day: date, cutoff: time) -> pd.DataFrame:
    """Latest forecast published at or before the cutoff, for each target time of the day.

    Normally a single run covers the whole day; picking per target time means a run that only
    partly covers the day still contributes, with older runs filling the rest.
    Columns: start_time, forecast_mw, publish_time.
    """
    start, end = delivery_window(delivery_day)
    cut = decision_cutoff(delivery_day, cutoff)
    available = forecasts[
        (forecasts["publish_time"] <= cut)
        & (forecasts["start_time"] >= start)
        & (forecasts["start_time"] < end)
    ]
    latest = available.sort_values("publish_time").drop_duplicates("start_time", keep="last")
    return (
        latest.rename(columns={"generation_mw": "forecast_mw"})
        .sort_values("start_time")[["start_time", "forecast_mw", "publish_time"]]
        .reset_index(drop=True)
    )


def decision_errors(
    forecasts: pd.DataFrame, actuals: pd.DataFrame, days: Iterable[date], cutoff: time
) -> pd.DataFrame:
    """Decision-time forecast vs actual for many delivery days, one row per matched target time.

    Forecasts are hourly and actuals half-hourly, so we compare only where both exist
    (on the hour) rather than inventing values by interpolation.
    Columns: delivery_date, start_time, publish_time, forecast_mw, actual_mw, error_mw,
             lead_hours, hour (UK local hour of day).
    """
    frames = []
    for day in days:
        frame = decision_forecast(forecasts, day, cutoff)
        frame["delivery_date"] = day
        frames.append(frame)
    joined = pd.concat(frames, ignore_index=True).merge(
        actuals.rename(columns={"generation_mw": "actual_mw"}), on="start_time", how="inner"
    )
    joined["error_mw"] = joined["forecast_mw"] - joined["actual_mw"]
    joined["lead_hours"] = (joined["start_time"] - joined["publish_time"]) / HOUR
    joined["hour"] = joined["start_time"].dt.tz_convert(UK_TZ).dt.hour
    return joined


# ---- summary statistics ----------------------------------------------------------------


def _num(value: float | np.floating | None) -> float | None:
    """Plain float for JSON, with NaN turned into None."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def error_summary(errors: pd.Series, actuals: pd.Series) -> dict:
    """Headline accuracy numbers for a set of (forecast - actual) errors."""
    if errors.empty:
        return {"n": 0}
    abs_err = errors.abs()
    mean_actual = actuals.mean()
    mae = abs_err.mean()
    return {
        "n": int(len(errors)),
        "mae_mw": _num(mae),
        "rmse_mw": _num(np.sqrt((errors**2).mean())),
        "bias_mw": _num(errors.mean()),
        "median_abs_error_mw": _num(abs_err.median()),
        "p90_abs_error_mw": _num(abs_err.quantile(0.9)),
        "max_over_forecast_mw": _num(max(errors.max(), 0.0)),
        "max_under_forecast_mw": _num(max(-errors.min(), 0.0)),
        "mean_actual_mw": _num(mean_actual),
        # MAE relative to average wind output: a scale-free "how big is the error" figure.
        "nmae_pct": _num(mae / mean_actual * 100) if mean_actual else None,
    }


def energy_mwh(times: pd.Series, mw: pd.Series) -> float | None:
    """Energy over a period from evenly spaced MW values (MW x hours per step)."""
    if mw.isna().all():
        return None
    # A single point has no spacing to measure; assume the hourly forecast resolution.
    step_hours = times.sort_values().diff().median() / HOUR if len(times) > 1 else 1.0
    return _num(mw.sum() * step_hours)


def error_by_hour(errors: pd.DataFrame) -> list[dict]:
    """Error spread for each hour of the delivery day (later hours = longer lead time)."""
    rows = []
    for hour, group in errors.groupby("hour"):
        err = group["error_mw"]
        rows.append(
            {
                "hour": int(hour),
                "n": int(len(err)),
                "mae_mw": _num(err.abs().mean()),
                "mean_error_mw": _num(err.mean()),
                "p10_error_mw": _num(err.quantile(0.10)),
                "p50_error_mw": _num(err.quantile(0.50)),
                "p90_error_mw": _num(err.quantile(0.90)),
            }
        )
    return rows


def error_by_day(errors: pd.DataFrame, actuals: pd.DataFrame) -> list[dict]:
    """One row per delivery day: how good was that day's decision forecast."""
    rows = []
    for day, group in errors.groupby("delivery_date"):
        start, end = delivery_window(day)
        day_actuals = actuals[(actuals["start_time"] >= start) & (actuals["start_time"] < end)]
        err = group["error_mw"]
        rows.append(
            {
                "delivery_date": day,
                "n": int(len(err)),
                "mae_mw": _num(err.abs().mean()),
                "bias_mw": _num(err.mean()),
                "max_abs_error_mw": _num(err.abs().max()),
                "forecast_energy_mwh": energy_mwh(group["start_time"], group["forecast_mw"]),
                "actual_energy_mwh": energy_mwh(
                    day_actuals["start_time"], day_actuals["generation_mw"]
                ),
            }
        )
    return rows


def error_histogram(errors: pd.Series, bin_mw: float = 500.0) -> list[dict]:
    if errors.empty:
        return []
    low = math.floor(errors.min() / bin_mw) * bin_mw
    high = math.ceil(errors.max() / bin_mw) * bin_mw
    if high == low:
        high = low + bin_mw
    edges = np.arange(low, high + bin_mw, bin_mw)
    counts, edges = np.histogram(errors, bins=edges)
    return [
        {"lower_mw": float(lo), "upper_mw": float(hi), "count": int(c)}
        for lo, hi, c in zip(edges[:-1], edges[1:], counts, strict=True)
    ]


def error_by_lead_time(
    forecasts: pd.DataFrame,
    actuals: pd.DataFrame,
    first_day: date,
    last_day: date,
    max_lead_hours: int = MAX_LEAD_HOURS,
) -> list[dict]:
    """Accuracy of ALL forecast runs, grouped by how far ahead they were made (1-hour buckets).

    Shows how much accuracy the desk gives up by deciding a day ahead instead of closer to
    delivery.
    """
    start, _ = delivery_window(first_day)
    _, end = delivery_window(last_day)
    targets = forecasts[(forecasts["start_time"] >= start) & (forecasts["start_time"] < end)]
    pairs = targets.merge(
        actuals.rename(columns={"generation_mw": "actual_mw"}), on="start_time", how="inner"
    )
    pairs["lead_hours"] = (pairs["start_time"] - pairs["publish_time"]) / HOUR
    pairs = pairs[(pairs["lead_hours"] >= 0) & (pairs["lead_hours"] <= max_lead_hours)]
    pairs["error_mw"] = pairs["generation_mw"] - pairs["actual_mw"]
    pairs["bucket"] = np.floor(pairs["lead_hours"]).astype(int)
    rows = []
    for bucket, group in pairs.groupby("bucket"):
        err = group["error_mw"]
        rows.append(
            {
                "lead_hours": int(bucket),
                "n": int(len(err)),
                "mae_mw": _num(err.abs().mean()),
                "rmse_mw": _num(np.sqrt((err**2).mean())),
                "bias_mw": _num(err.mean()),
                "p90_abs_error_mw": _num(err.abs().quantile(0.9)),
            }
        )
    return rows


# ---- uncertainty band for one day ------------------------------------------------------


def error_quantiles_by_hour(errors: pd.DataFrame) -> pd.DataFrame:
    """10th/90th percentile of error for each hour of day (index: hour)."""
    grouped = errors.groupby("hour")["error_mw"]
    return pd.DataFrame(
        {"q_low": grouped.quantile(BAND_LOW_Q), "q_high": grouped.quantile(BAND_HIGH_Q)}
    )


def add_uncertainty_band(day_forecast: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Add low/high wind cases to a day's forecast using past errors at the same hour.

    actual = forecast - error, so the LOW wind case uses the HIGH error quantile.
    `history` should exclude the day itself so the band is not built from its own outcome.
    Wind output cannot be negative, so the band is clipped at zero.
    """
    out = day_forecast.copy()
    if history.empty or out.empty:
        out["low_mw"] = np.nan
        out["high_mw"] = np.nan
        return out
    quantiles = error_quantiles_by_hour(history)
    overall = history["error_mw"].quantile([BAND_LOW_Q, BAND_HIGH_Q]).to_numpy()
    hours = out["start_time"].dt.tz_convert(UK_TZ).dt.hour
    q_low = hours.map(quantiles["q_low"]).fillna(overall[0])
    q_high = hours.map(quantiles["q_high"]).fillna(overall[1])
    out["low_mw"] = (out["forecast_mw"] - q_high).clip(lower=0)
    out["high_mw"] = (out["forecast_mw"] - q_low).clip(lower=0)
    return out


# ---- forecast runs around one day ------------------------------------------------------


def runs_for_day(
    forecasts: pd.DataFrame, delivery_day: date, cutoff: time, lookback_hours: int = 24
) -> list[dict]:
    """Forecast runs covering the delivery day, from a day before the cutoff to delivery end.

    Lets the desk see how the forecast moved before and after their decision.
    """
    start, end = delivery_window(delivery_day)
    cut = decision_cutoff(delivery_day, cutoff)
    window = forecasts[
        (forecasts["start_time"] >= start)
        & (forecasts["start_time"] < end)
        & (forecasts["publish_time"] >= cut - pd.Timedelta(hours=lookback_hours))
        & (forecasts["publish_time"] < end)
    ]
    runs = []
    for publish_time, group in window.groupby("publish_time"):
        group = group.sort_values("start_time")
        runs.append(
            {
                "publish_time": publish_time.to_pydatetime(),
                "after_cutoff": bool(publish_time > cut),
                "points": [
                    {"time": t.to_pydatetime(), "forecast_mw": _num(mw)}
                    for t, mw in zip(group["start_time"], group["generation_mw"], strict=True)
                ],
            }
        )
    return runs
