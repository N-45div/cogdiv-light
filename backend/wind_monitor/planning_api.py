"""HTTP routes for planning: the clock, the outlook at "now", and flexible-load plans.

"Now" comes from the clock, so in replay mode these answer exactly as they would have at that
moment in January 2026 (no look-ahead), and in live mode they use real time and live data.
"""

from __future__ import annotations

import hmac
import math
import os
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from . import analysis as an
from . import config
from . import outlook as ol
from .clock import Clock, as_utc, get_clock
from .flex import (
    DEFAULT_DEADLINE,
    MAX_PLAN_HOURS,
    FlexJob,
    Plan,
    blocks,
    next_uk_time,
    plan_load,
    wind_during,
)
from .schemas import ClockOut, ClockUpdate, OutlookView, PlanRequest, PlanView
from .store import WindData, get_live_window, get_wind_data

router = APIRouter(prefix="/api")

ClockDep = Annotated[Clock, Depends(get_clock)]


def require_app_token(x_app_token: Annotated[str | None, Header()] = None) -> None:
    """Guards endpoints that cost money or change shared state (ask the agent, move the clock,
    send a plan to a device). Set APP_TOKEN on a public deployment; unset, they are open."""
    expected = os.environ.get("APP_TOKEN")
    if expected and not hmac.compare_digest(expected, x_app_token or ""):
        raise HTTPException(401, "Missing or wrong X-App-Token")


def planning_data(clock: ClockDep) -> WindData:
    """Replay plans on the January snapshot; live mode on the rolling live window."""
    if clock.mode == "live":
        return get_live_window().get(clock.now())
    return get_wind_data()


PlanningDataDep = Annotated[WindData, Depends(planning_data)]


# ---- clock ------------------------------------------------------------------------------


@router.get("/clock", response_model=ClockOut)
def read_clock(clock: ClockDep) -> dict:
    return clock.state()


@router.post("/clock", response_model=ClockOut, dependencies=[Depends(require_app_token)])
def move_clock(update: ClockUpdate, clock: ClockDep) -> dict:
    """Switch between live and replay; in replay, jump to a time, change speed, or step."""
    if update.mode is not None and update.mode != clock.mode:
        if not hasattr(clock, "switch"):
            raise HTTPException(409, "This clock can't switch modes")
        clock.switch(update.mode)
    moving = update.at is not None or update.speed is not None or update.advance_minutes
    if moving and clock.mode != "replay":
        raise HTTPException(409, "The clock can only be moved in replay mode")
    try:
        if update.at is not None or update.speed is not None:
            clock.set(at=update.at, speed=update.speed)
        if update.advance_minutes:
            clock.advance(pd.Timedelta(minutes=update.advance_minutes))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return clock.state()


# ---- outlook ----------------------------------------------------------------------------


def _band_note(history: pd.DataFrame) -> str:
    if len(history) < an.MIN_BAND_SAMPLES:
        return "Not enough past errors yet: no bias correction or range is given."
    return (
        f"Expected = forecast scaled by how metered wind compared with forecasts made as far "
        f"ahead (median actual/forecast); range = 10th-90th percentile of that ratio, from "
        f"{len(history):,} forecast/actual pairs over the last {config.HISTORY_DAYS} days."
    )


@router.get("/outlook", response_model=OutlookView)
def outlook_view(
    data: PlanningDataDep,
    clock: ClockDep,
    hours: Annotated[int, Query(ge=1, le=MAX_PLAN_HOURS)] = 24,
    recent_hours: Annotated[float, Query(gt=0, le=48)] = 6,
) -> dict:
    """The wind outlook as of now: the newest forecast, corrected for its usual bias, with a
    likely range, and how actual wind has tracked the forecast over the last few hours."""
    now = clock.now()
    forecast, history = ol.outlook(data, now, hours)
    tracking = ol.recent_tracking(data, now, recent_hours)
    published = ol.published_forecasts(data.forecasts, now)["publish_time"]
    return {
        "as_of": now.to_pydatetime(),
        "mode": clock.mode,
        "data_source": data.source,
        "latest_run": published.max().to_pydatetime() if len(published) else None,
        "latest_actual": ol.latest_actual(data, now),
        "history": {
            "days": config.HISTORY_DAYS,
            "n": len(history),
            "by_lead": ol.history_summary(history),
        },
        "band_note": _band_note(history),
        "points": [
            {
                "time": row.start_time.to_pydatetime(),
                "forecast_mw": an._num(row.forecast_mw),
                "expected_mw": an._num(row.expected_mw),
                "low_mw": an._num(row.low_mw),
                "high_mw": an._num(row.high_mw),
                "lead_hours": float(row.lead_hours),
                "publish_time": row.publish_time.to_pydatetime(),
            }
            for row in forecast.itertuples(index=False)
        ],
        "recent": {
            "hours": recent_hours,
            "mean_error_mw": an._num(tracking["error_mw"].mean()) if len(tracking) else None,
            "points": [
                {
                    "time": row.start_time.to_pydatetime(),
                    "actual_mw": an._num(row.actual_mw),
                    "forecast_mw": an._num(row.forecast_mw),
                    "error_mw": an._num(row.error_mw),
                    "lead_hours": float(row.lead_hours),
                }
                for row in tracking.itertuples(index=False)
            ],
        },
    }


# ---- plans ------------------------------------------------------------------------------


def _wind(slots: pd.DataFrame) -> dict:
    return {col: wind_during(slots, col) for col in ("forecast_mw", "expected_mw", "low_mw")}


def _notes(plan: Plan, history: pd.DataFrame) -> list[str]:
    notes = []
    used = plan.slots[plan.slots["energy_kwh"] > 0]
    unknown = int(used["forecast_mw"].isna().sum())
    if unknown:
        notes.append(f"{unknown} half-hour(s) in the plan are beyond the newest forecast.")
    if plan.basis != "raw" and len(history) < an.MIN_BAND_SAMPLES:
        notes.append("Not enough past errors yet: the raw forecast was used.")
    if not plan.feasible:
        notes.append(
            f"The job cannot finish in time: {plan.shortfall_kwh:.1f} kWh short at "
            f"{plan.job.power_kw:g} kW."
        )
    return notes


@router.post("/plan", response_model=PlanView)
def make_plan(request: PlanRequest, data: PlanningDataDep, clock: ClockDep) -> dict:
    """Schedule a flexible job (e.g. an EV charge) into the windiest half-hours before its
    deadline, using only what is known now."""
    now = clock.now()
    earliest = max(as_utc(request.earliest), now) if request.earliest else now
    deadline = (
        as_utc(request.deadline) if request.deadline else next_uk_time(earliest, DEFAULT_DEADLINE)
    )
    if deadline <= earliest:
        raise HTTPException(422, "deadline must be after the start (and after now)")
    if deadline - now > pd.Timedelta(hours=MAX_PLAN_HOURS):
        raise HTTPException(422, f"Plans can reach at most {MAX_PLAN_HOURS} hours ahead")

    hours = math.ceil((deadline - now.floor("h")) / an.HOUR)
    forecast, history = ol.outlook(data, now, hours)
    job = FlexJob(request.energy_kwh, request.power_kw, earliest, deadline, request.interruptible)
    plan = plan_load(forecast, job, request.basis)
    used = forecast["publish_time"]
    return {
        "as_of": now.to_pydatetime(),
        "basis": plan.basis,
        "job": {
            "energy_kwh": job.energy_kwh,
            "power_kw": job.power_kw,
            "earliest": earliest.to_pydatetime(),
            "deadline": deadline.to_pydatetime(),
            "interruptible": job.interruptible,
        },
        "feasible": plan.feasible,
        "planned_kwh": round(plan.planned_kwh, 3),
        "shortfall_kwh": round(plan.shortfall_kwh, 3),
        "blocks": [
            {**b, "start": b["start"].to_pydatetime(), "end": b["end"].to_pydatetime()}
            for b in blocks(plan.slots)
        ],
        "wind": {"planned": _wind(plan.slots), "asap": _wind(plan.asap)},
        "forecast_run": used.max().to_pydatetime() if len(used) else None,
        "notes": _notes(plan, history),
        "slots": [
            {
                "time": row.start_time.to_pydatetime(),
                "on": bool(row.energy_kwh > 0),
                "energy_kwh": round(float(row.energy_kwh), 3),
                "forecast_mw": an._num(row.forecast_mw),
                "expected_mw": an._num(row.expected_mw),
                "low_mw": an._num(row.low_mw),
                "high_mw": an._num(row.high_mw),
                "score_mw": an._num(row.score_mw),
            }
            for row in plan.slots.itertuples(index=False)
        ],
    }
