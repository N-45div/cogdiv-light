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
HALF_HOUR = pd.Timedelta(minutes=30)
# Below this many past errors, percentiles are too noisy to show.
MIN_BAND_SAMPLES = 10
# Likely range of the actual, from past errors: 10th-90th percentile (an 80% range), and the
# median for the bias-adjusted "expected" value.
BAND_LOW_Q = 0.10
BAND_MID_Q = 0.50
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


def forecast_as_of(
    forecasts: pd.DataFrame, as_of: pd.Timestamp, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Latest forecast published at or before `as_of`, for each target time in [start, end).

    This is "what the forecast said at that moment", with no look-ahead. Picking per target
    time means a run that only partly covers the window still contributes, with older runs
    filling the rest. Columns: start_time, forecast_mw, publish_time.
    """
    available = forecasts[
        (forecasts["publish_time"] <= as_of)
        & (forecasts["start_time"] >= start)
        & (forecasts["start_time"] < end)
    ]
    latest = available.sort_values("publish_time").drop_duplicates("start_time", keep="last")
    return (
        latest.rename(columns={"generation_mw": "forecast_mw"})
        .sort_values("start_time")[["start_time", "forecast_mw", "publish_time"]]
        .reset_index(drop=True)
    )


def decision_forecast(forecasts: pd.DataFrame, delivery_day: date, cutoff: time) -> pd.DataFrame:
    """The forecast the desk had at the cutoff, for each hour of the delivery day.

    Columns: start_time, forecast_mw, publish_time.
    """
    start, end = delivery_window(delivery_day)
    return forecast_as_of(forecasts, decision_cutoff(delivery_day, cutoff), start, end)


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
    if not frames:  # no days asked for: an empty table that still has the right columns
        frames = [decision_forecast(forecasts.iloc[:0], date(2000, 1, 1), cutoff)]
        frames[0]["delivery_date"] = pd.Series(dtype=object)
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
        # Share of hours where the forecast was too high (desk short).
        "over_forecast_share_pct": _num((errors > 0).mean() * 100),
        # Average error left after removing the constant offset (the bias): the "random" part.
        "debiased_mae_mw": _num((errors - errors.mean()).abs().mean()),
        # MAE relative to average wind output: a scale-free "how big is the error" figure.
        "nmae_pct": _num(mae / mean_actual * 100) if mean_actual else None,
    }


def expected_periods(day: date, step: pd.Timedelta) -> int:
    """How many values a full delivery day has at this resolution (clock changes included)."""
    start, end = delivery_window(day)
    return int((end - start) / step)


def day_energy_mwh(times: pd.Series, mw: pd.Series, day: date, step: pd.Timedelta) -> float | None:
    """Energy for a whole delivery day from MW values every `step`, or None if any are missing.

    A total built from partial data would silently understate the day, so we don't give one.
    """
    if times[mw.notna()].nunique() < expected_periods(day, step):
        return None
    return _num(mw.sum() * (step / HOUR))


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
                # Forecast total over hours that also have an actual, so both sides match.
                "forecast_energy_mwh": day_energy_mwh(
                    group["start_time"], group["forecast_mw"], day, HOUR
                ),
                "actual_energy_mwh": day_energy_mwh(
                    day_actuals["start_time"], day_actuals["generation_mw"], day, HALF_HOUR
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


def run_errors(
    forecasts: pd.DataFrame,
    actuals: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    max_lead_hours: float = MAX_LEAD_HOURS,
) -> pd.DataFrame:
    """Error of EVERY forecast run for target times in [start, end), not just the decision run.

    Only real forecasts count: values a run gives for hours already past when it was published
    (lead < 0) are left out. Columns: publish_time, start_time, forecast_mw, actual_mw,
    error_mw, lead_hours.
    """
    targets = forecasts[(forecasts["start_time"] >= start) & (forecasts["start_time"] < end)]
    pairs = targets.rename(columns={"generation_mw": "forecast_mw"}).merge(
        actuals.rename(columns={"generation_mw": "actual_mw"}), on="start_time", how="inner"
    )
    pairs["lead_hours"] = (pairs["start_time"] - pairs["publish_time"]) / HOUR
    pairs = pairs[(pairs["lead_hours"] >= 0) & (pairs["lead_hours"] <= max_lead_hours)].copy()
    pairs["error_mw"] = pairs["forecast_mw"] - pairs["actual_mw"]
    return pairs.reset_index(drop=True)


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
    pairs = run_errors(forecasts, actuals, start, end, max_lead_hours)
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


def error_quantiles(errors: pd.DataFrame, by: str, column: str = "error_mw") -> pd.DataFrame:
    """10th/50th/90th percentile of `column` for each value of column `by` (index: that value).

    Groups with fewer than MIN_BAND_SAMPLES rows are left out (callers fall back to all).
    """
    grouped = errors.groupby(by, observed=True)[column]
    quantiles = pd.DataFrame(
        {
            "q_low": grouped.quantile(BAND_LOW_Q),
            "q_mid": grouped.quantile(BAND_MID_Q),
            "q_high": grouped.quantile(BAND_HIGH_Q),
        }
    )
    return quantiles[grouped.size() >= MIN_BAND_SAMPLES]


def error_quantiles_by_hour(errors: pd.DataFrame) -> pd.DataFrame:
    """10th/50th/90th percentile of error for each hour of day (index: hour)."""
    return error_quantiles(errors, "hour")


def group_quantiles(
    keys: pd.Series, history: pd.DataFrame, by: str, column: str = "error_mw"
) -> tuple[pd.Series, pd.Series, pd.Series] | None:
    """10th/50th/90th percentile of `column` in each row's group (`keys` matches `by`).

    A group with too few rows uses all of `history` instead. None if `history` is too small.
    """
    if len(history) < MIN_BAND_SAMPLES:
        return None
    quantiles = error_quantiles(history, by, column)
    overall = history[column].quantile([BAND_LOW_Q, BAND_MID_Q, BAND_HIGH_Q]).to_numpy()
    keys = pd.Series(keys.to_numpy(), index=keys.index, dtype=object)
    return tuple(
        keys.map(quantiles[col].to_dict()).astype(float).fillna(overall[i])
        for i, col in enumerate(("q_low", "q_mid", "q_high"))
    )


def no_band(forecast: pd.DataFrame) -> pd.DataFrame:
    out = forecast.copy()
    for col in ("expected_mw", "low_mw", "high_mw"):
        out[col] = np.nan
    return out


def apply_error_band(
    forecast: pd.DataFrame, keys: pd.Series, history: pd.DataFrame, by: str
) -> pd.DataFrame:
    """Add the likely range of the actual to a forecast, from past errors in the same group.

    `keys` gives each forecast row's group (e.g. its hour of day), matching column `by` of
    `history`. actual = forecast - error, so:
      expected_mw = forecast - median error   (forecast adjusted for its typical bias)
      low_mw      = forecast - 90th pct error (low wind case)
      high_mw     = forecast - 10th pct error (high wind case)
    A group with too few past errors uses all of them instead; with too little history overall
    no range is given. Wind output cannot be negative, so values are clipped at zero.
    """
    q = None if forecast.empty else group_quantiles(keys, history, by)
    if q is None:
        return no_band(forecast)
    q_low, q_mid, q_high = (s.set_axis(forecast.index) for s in q)
    out = forecast.copy()
    out["expected_mw"] = (out["forecast_mw"] - q_mid).clip(lower=0)
    out["low_mw"] = (out["forecast_mw"] - q_high).clip(lower=0)
    out["high_mw"] = (out["forecast_mw"] - q_low).clip(lower=0)
    return out


def add_uncertainty_band(day_forecast: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Add the likely range of the actual to a day's forecast, using past errors at that hour.

    `history` should exclude the day itself so the range is not built from its own outcome.
    See `apply_error_band` for the formulas and fallbacks.
    """
    if day_forecast.empty:
        return apply_error_band(day_forecast, pd.Series(dtype=object), history, "hour")
    hours = day_forecast["start_time"].dt.tz_convert(UK_TZ).dt.hour
    return apply_error_band(day_forecast, hours, history, "hour")


# ---- forecast runs around one day ------------------------------------------------------


def runs_for_day(
    forecasts: pd.DataFrame, delivery_day: date, cutoff: time, lookback_hours: int = 24
) -> list[dict]:
    """Forecast runs covering the delivery day, from a day before the cutoff to delivery end.

    Lets the desk see how the forecast moved before and after their decision. WINDFOR runs
    also carry values for hours already past when published; those are not forecasts, so
    only points at or after each run's publish time are kept.
    """
    start, end = delivery_window(delivery_day)
    cut = decision_cutoff(delivery_day, cutoff)
    window = forecasts[
        (forecasts["start_time"] >= start)
        & (forecasts["start_time"] < end)
        & (forecasts["publish_time"] >= cut - pd.Timedelta(hours=lookback_hours))
        & (forecasts["publish_time"] < end)
        & (forecasts["start_time"] >= forecasts["publish_time"])
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
