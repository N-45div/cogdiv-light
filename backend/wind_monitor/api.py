"""HTTP routes. Each route is thin: validate input, call analysis functions, shape the output."""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from . import analysis as an
from . import config
from .schemas import AccuracyView, DayView, Meta
from .store import WindData, get_wind_data

router = APIRouter(prefix="/api")

DataDep = Annotated[WindData, Depends(get_wind_data)]
CutoffQuery = Annotated[
    str,
    Query(
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="Decision cutoff, UK local time HH:MM on the day before delivery",
    ),
]


def _parse_cutoff(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _month_errors(data: WindData, cutoff: time) -> pd.DataFrame:
    """Decision-forecast errors for every delivery day in scope (cached per cutoff)."""
    key = ("errors", cutoff)
    if key not in data.cache:
        days = an.date_range(config.DELIVERY_FIRST, config.DELIVERY_LAST)
        data.cache[key] = an.decision_errors(data.forecasts, data.actuals, days, cutoff)
    return data.cache[key]


def _lead_time_profile(data: WindData) -> list[dict]:
    key = ("lead_time",)
    if key not in data.cache:
        data.cache[key] = an.error_by_lead_time(
            data.forecasts, data.actuals, config.DELIVERY_FIRST, config.DELIVERY_LAST
        )
    return data.cache[key]


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/meta", response_model=Meta)
def meta(data: DataDep) -> dict:
    actual_times = data.actuals["start_time"]
    return {
        "timezone": str(config.UK_TZ),
        "decision_first": config.DECISION_FIRST,
        "decision_last": config.DECISION_LAST,
        "delivery_first": config.DELIVERY_FIRST,
        "delivery_last": config.DELIVERY_LAST,
        "default_cutoff": config.DEFAULT_CUTOFF.strftime("%H:%M"),
        "cutoff_presets": [
            {"key": p.key, "label": p.label, "time": p.time.strftime("%H:%M")}
            for p in config.CUTOFF_PRESETS
        ],
        "data": {
            "source": data.source,
            "retrieved_at": data.retrieved_at,
            "actuals_rows": len(data.actuals),
            "forecast_rows": len(data.forecasts),
            "forecast_runs": int(data.forecasts["publish_time"].nunique()),
            "actuals_first": actual_times.min() if len(actual_times) else None,
            "actuals_last": actual_times.max() if len(actual_times) else None,
        },
    }


@router.get("/day", response_model=DayView)
def day_view(data: DataDep, delivery_date: date, cutoff: CutoffQuery = "09:20") -> dict:
    """Everything the desk needs for one delivery day: the forecast they would have bought
    against, its uncertainty band, what actually happened, and how the forecast moved."""
    if not config.DELIVERY_FIRST <= delivery_date <= config.DELIVERY_LAST:
        raise HTTPException(
            422,
            f"delivery_date must be between {config.DELIVERY_FIRST} and {config.DELIVERY_LAST}",
        )
    cut = _parse_cutoff(cutoff)
    start, end = an.delivery_window(delivery_date)

    history = _month_errors(data, cut)
    other_days = history[history["delivery_date"] != delivery_date]
    forecast = an.add_uncertainty_band(
        an.decision_forecast(data.forecasts, delivery_date, cut), other_days
    )
    actual = data.actuals[
        (data.actuals["start_time"] >= start) & (data.actuals["start_time"] < end)
    ].rename(columns={"generation_mw": "actual_mw"})

    points = actual.merge(forecast, on="start_time", how="outer").sort_values("start_time")
    points["error_mw"] = points["forecast_mw"] - points["actual_mw"]
    day_errors = history[history["delivery_date"] == delivery_date]

    decision_run = None
    if not forecast.empty:
        leads = (forecast["start_time"] - forecast["publish_time"]) / an.HOUR
        decision_run = {
            "publish_time": forecast["publish_time"].max().to_pydatetime(),
            "lead_hours_min": float(leads.min()),
            "lead_hours_max": float(leads.max()),
        }

    n_days = other_days["delivery_date"].nunique()
    return {
        "delivery_date": delivery_date,
        "decision_date": delivery_date - timedelta(days=1),
        "cutoff": cutoff,
        "cutoff_time": an.decision_cutoff(delivery_date, cut).to_pydatetime(),
        "decision_run": decision_run,
        "summary": {
            **{
                f"{name}_energy_mwh": an.day_energy_mwh(
                    forecast["start_time"], forecast[f"{name}_mw"], delivery_date, an.HOUR
                )
                for name in ("forecast", "expected", "low", "high")
            },
            "actual_energy_mwh": an.day_energy_mwh(
                actual["start_time"], actual["actual_mw"], delivery_date, an.HALF_HOUR
            ),
            "forecast_hours": int(forecast["forecast_mw"].notna().sum()),
            "expected_hours": an.expected_periods(delivery_date, an.HOUR),
            "actual_periods": int(actual["actual_mw"].notna().sum()),
            "expected_periods": an.expected_periods(delivery_date, an.HALF_HOUR),
            "peak_forecast_mw": an._num(forecast["forecast_mw"].max()),
            "min_forecast_mw": an._num(forecast["forecast_mw"].min()),
            "accuracy": an.error_summary(day_errors["error_mw"], day_errors["actual_mw"]),
        },
        "band_note": (
            "Expected = forecast adjusted by its median error at that hour; shaded = 80% range "
            f"(10th-90th percentile of errors), both from the other {n_days} delivery days."
        ),
        "points": [
            {
                "time": row.start_time.to_pydatetime(),
                "actual_mw": an._num(row.actual_mw),
                "forecast_mw": an._num(row.forecast_mw),
                "expected_mw": an._num(row.expected_mw),
                "low_mw": an._num(row.low_mw),
                "high_mw": an._num(row.high_mw),
                "error_mw": an._num(row.error_mw),
            }
            for row in points.itertuples(index=False)
        ],
        "runs": an.runs_for_day(data.forecasts, delivery_date, cut),
    }


@router.get("/accuracy", response_model=AccuracyView)
def accuracy_view(data: DataDep, cutoff: CutoffQuery = "09:20") -> dict:
    """How accurate the decision-time forecast was across all delivery days in scope."""
    key = ("accuracy", cutoff)
    if key not in data.cache:  # the data never changes, so compute once per cut-off
        data.cache[key] = _accuracy(data, cutoff)
    return data.cache[key]


def _accuracy(data: WindData, cutoff: str) -> dict:
    errors = _month_errors(data, _parse_cutoff(cutoff))
    return {
        "cutoff": cutoff,
        "summary": an.error_summary(errors["error_mw"], errors["actual_mw"]),
        "daily": an.error_by_day(errors, data.actuals),
        "by_hour": an.error_by_hour(errors),
        "by_lead_time": _lead_time_profile(data),
        "decision_lead_hours": {
            "min": an._num(errors["lead_hours"].min()) if len(errors) else None,
            "max": an._num(errors["lead_hours"].max()) if len(errors) else None,
        },
        "histogram": an.error_histogram(errors["error_mw"]),
    }
