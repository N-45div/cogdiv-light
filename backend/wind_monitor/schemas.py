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
    # Data coverage: daily energy totals are only given when the day is complete.
    forecast_hours: int = Field(description="Hours of the day with a decision forecast")
    expected_hours: int = Field(description="Hours in the delivery day (23-25 on clock changes)")
    actual_periods: int = Field(description="Half-hours of the day with an actual value")
    expected_periods: int = Field(description="Half-hours in the delivery day (46-50)")
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


# ---- planning: clock, outlook, flexible-load plans --------------------------------------


class ClockOut(BaseModel):
    mode: str = Field(description="'replay' (moving through January 2026) or 'live'")
    now: datetime = Field(description="The moment everything is planned 'as of'")
    speed: float = Field(description="Simulated seconds per real second (0 = paused)")
    first: datetime | None = Field(None, description="Earliest replay time")
    last: datetime | None = Field(None, description="Latest replay time")


class ClockUpdate(BaseModel):
    at: datetime | None = Field(None, description="Jump to this time (no zone = UK local)")
    speed: float | None = Field(None, ge=0, description="Simulated seconds per real second")
    advance_minutes: float | None = Field(None, description="Move forward (or back) this much")


class LeadBucketStats(BaseModel):
    lead_bucket: str = Field(description="How far ahead the forecasts were made, e.g. '0-6h'")
    n: int
    bias_mw: float | None = Field(None, description="Mean of forecast - actual (+ = too high)")
    mae_mw: float | None = None
    p10_error_mw: float | None = None
    p90_error_mw: float | None = None
    over_forecast_share_pct: float | None = None
    actual_share_pct: float | None = Field(
        None, description="Metered wind as % of the forecast (median); the forecast is scaled by it"
    )


class ErrorHistory(BaseModel):
    days: float = Field(description="Rolling window of past errors used for the band")
    n: int = Field(description="Forecast/actual pairs in the window")
    by_lead: list[LeadBucketStats]


class OutlookPoint(BaseModel):
    time: datetime = Field(description="Start of the hour (UTC)")
    forecast_mw: float | None
    expected_mw: float | None = Field(
        None, description="Forecast scaled by how metered wind usually compares with it"
    )
    low_mw: float | None = Field(None, description="Low wind case (90% chance of more)")
    high_mw: float | None = Field(None, description="High wind case (10% chance of more)")
    lead_hours: float = Field(description="How far ahead this forecast was made")
    publish_time: datetime


class TrackingPoint(BaseModel):
    time: datetime
    actual_mw: float | None
    forecast_mw: float | None = Field(None, description="Freshest forecast made before the hour")
    error_mw: float | None = Field(None, description="forecast - actual (+ = wind below forecast)")
    lead_hours: float


class RecentTracking(BaseModel):
    hours: float
    mean_error_mw: float | None = Field(
        None, description="Average forecast - actual over the period (+ = wind below forecast)"
    )
    points: list[TrackingPoint]


class LatestActual(BaseModel):
    time: datetime
    actual_mw: float | None


class OutlookView(BaseModel):
    as_of: datetime
    mode: str
    data_source: str
    latest_run: datetime | None = Field(None, description="Newest forecast run published so far")
    latest_actual: LatestActual | None
    history: ErrorHistory
    band_note: str
    points: list[OutlookPoint]
    recent: RecentTracking


class PlanRequest(BaseModel):
    energy_kwh: float = Field(gt=0, le=500, description="Energy the job needs")
    power_kw: float = Field(gt=0, le=100, description="Most power the device can draw")
    earliest: datetime | None = Field(None, description="Not before (default: now)")
    deadline: datetime | None = Field(None, description="Done by (default: next 07:00 UK)")
    interruptible: bool = Field(True, description="Can it pause and resume (EV: yes)")
    basis: str = Field(
        "expected",
        pattern="^(raw|expected|cautious)$",
        description="raw = forecast as is; expected = bias-corrected; cautious = low-wind case",
    )


class PlanJob(BaseModel):
    energy_kwh: float
    power_kw: float
    earliest: datetime
    deadline: datetime
    interruptible: bool


class PlanBlock(BaseModel):
    start: datetime
    end: datetime
    energy_kwh: float


class WindDuring(BaseModel):
    forecast_mw: float | None
    expected_mw: float | None
    low_mw: float | None


class PlanWind(BaseModel):
    planned: WindDuring = Field(description="Average wind while the plan runs (energy-weighted)")
    asap: WindDuring = Field(description="The same, if the job simply started straight away")


class PlanSlot(BaseModel):
    time: datetime
    on: bool
    energy_kwh: float
    forecast_mw: float | None
    expected_mw: float | None
    low_mw: float | None
    high_mw: float | None
    score_mw: float | None = Field(None, description="The value used to rank this slot")


class PlanView(BaseModel):
    as_of: datetime
    basis: str
    job: PlanJob
    feasible: bool
    planned_kwh: float
    shortfall_kwh: float
    blocks: list[PlanBlock]
    wind: PlanWind
    forecast_run: datetime | None = Field(None, description="Newest forecast run used")
    notes: list[str]
    slots: list[PlanSlot]


# ---- agent ------------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500, description="What to ask the agent")


class FactCheckClaim(BaseModel):
    text: str = Field(description="The figure as written in the answer")
    kind: str = Field(description="'time' or 'number'")
    supported: bool
    source: str | None = Field(None, description="The tool value it matched")


class FactCheckOut(BaseModel):
    passed: bool
    summary: str = Field(description="e.g. 'facts checked 5/5' or '2 facts don't match'")
    claims: list[FactCheckClaim]


class ToolCallOut(BaseModel):
    name: str
    arguments: dict | str | None
    result: dict


class AgentRunOut(BaseModel):
    id: str
    question: str
    as_of: str = Field(description="The clock time the agent planned at")
    mode: str
    answer: str | None
    verified: bool = Field(description="Every figure in the answer matched a tool result")
    factcheck: FactCheckOut | None
    models: list[str] = Field(description="Model behind each call, e.g. 'meta:muse-spark-1.3'")
    tool_calls: list[ToolCallOut]
    plan: dict | None = Field(None, description="The last plan_charge result, if any")
    schedule: list[dict] = Field(
        default_factory=list, description="That plan's charging windows, UTC (start, end, kWh)"
    )
    rewrites: int
    seconds: float
    error: str | None
    failed_models: list[dict]


class AgentRunSummary(BaseModel):
    id: str
    question: str
    as_of: str
    answer: str | None
    verified: bool
    summary: str | None
    models: list[str]
    seconds: float
