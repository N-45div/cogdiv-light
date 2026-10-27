"""API response models. They document the contract (see /docs) and validate what we send."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class CutoffPresetOut(BaseModel):
    key: str
    label: str
    time: str = Field(description="UK local time HH:MM on the day before delivery")


class DataInfo(BaseModel):
    source: str = Field(description="'snapshot' (saved file) or 'live' (Elexon API)")
    retrieved_at: datetime
    actuals_rows: int
    forecast_rows: int
    forecast_runs: int
    actuals_first: datetime | None
    actuals_last: datetime | None


class Meta(BaseModel):
    timezone: str
    decision_first: date
    decision_last: date
    delivery_first: date
    delivery_last: date
    default_cutoff: str
    cutoff_presets: list[CutoffPresetOut]
    data: DataInfo


class ErrorSummary(BaseModel):
    n: int = Field(description="Number of hourly forecast/actual pairs")
    mae_mw: float | None = None
    rmse_mw: float | None = None
    bias_mw: float | None = Field(None, description="Mean of forecast - actual (+ = over-forecast)")
    median_abs_error_mw: float | None = None
    p90_abs_error_mw: float | None = None
    max_over_forecast_mw: float | None = None
    max_under_forecast_mw: float | None = None
    mean_actual_mw: float | None = None
    nmae_pct: float | None = Field(None, description="MAE as % of mean actual wind output")
    over_forecast_share_pct: float | None = Field(
        None, description="% of hours where the forecast was above the actual"
    )
    debiased_mae_mw: float | None = Field(
        None, description="Mean absolute error after removing the average bias"
    )


class DayPoint(BaseModel):
    time: datetime = Field(description="Start of the period (UTC)")
    actual_mw: float | None = None
    forecast_mw: float | None = None
    expected_mw: float | None = Field(
        None, description="Forecast adjusted by the median past error at this hour"
    )
    low_mw: float | None = Field(None, description="Low wind case (10th percentile)")
    high_mw: float | None = Field(None, description="High wind case (90th percentile)")
    error_mw: float | None = None


class RunPoint(BaseModel):
    time: datetime
    forecast_mw: float | None


class ForecastRun(BaseModel):
    publish_time: datetime
    after_cutoff: bool
    points: list[RunPoint]


class DecisionRun(BaseModel):
    publish_time: datetime
    lead_hours_min: float
    lead_hours_max: float


class DaySummary(BaseModel):
    forecast_energy_mwh: float | None
    expected_energy_mwh: float | None
    low_energy_mwh: float | None
    high_energy_mwh: float | None
    actual_energy_mwh: float | None
    peak_forecast_mw: float | None
    min_forecast_mw: float | None
    accuracy: ErrorSummary


class DayView(BaseModel):
    delivery_date: date
    decision_date: date
    cutoff: str
    cutoff_time: datetime
    decision_run: DecisionRun | None = Field(
        None, description="The forecast run the desk would have used (None if none available)"
    )
    summary: DaySummary
    band_note: str
    points: list[DayPoint]
    runs: list[ForecastRun]


class DailyAccuracy(BaseModel):
    delivery_date: date
    n: int
    mae_mw: float | None
    bias_mw: float | None
    max_abs_error_mw: float | None
    forecast_energy_mwh: float | None
    actual_energy_mwh: float | None


class HourlyAccuracy(BaseModel):
    hour: int = Field(description="UK local hour of the delivery day")
    n: int
    mae_mw: float | None
    mean_error_mw: float | None
    p10_error_mw: float | None
    p50_error_mw: float | None
    p90_error_mw: float | None


class LeadTimeAccuracy(BaseModel):
    lead_hours: int = Field(description="Bucket start: forecasts made lead_hours..+1h ahead")
    n: int
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None
    p90_abs_error_mw: float | None


class HistogramBin(BaseModel):
    lower_mw: float
    upper_mw: float
    count: int


class LeadRange(BaseModel):
    min: float | None
    max: float | None


class AccuracyView(BaseModel):
    cutoff: str
    summary: ErrorSummary
    daily: list[DailyAccuracy]
    by_hour: list[HourlyAccuracy]
    by_lead_time: list[LeadTimeAccuracy]
    decision_lead_hours: LeadRange
    histogram: list[HistogramBin]
