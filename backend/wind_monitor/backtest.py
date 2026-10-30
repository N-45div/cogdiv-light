"""Backtest: does planning around the wind forecast actually catch more wind?

Every night a car plugs in and needs `energy_kwh` by the deadline. Each strategy decides when
it charges, using only what was known at the time, and is scored by the ACTUAL wind output
during the half-hours it charged (energy-weighted). Strategies:

    plug_in    charge straight away
    timer      a fixed start time (default 00:00), like a typical smart-charger timer
    once_raw   plan at plug-in on the forecast as published, never revisited
    once       plan at plug-in on the corrected forecast, never revisited
    replan     like `once`, but re-plan what is left each time a new forecast run is published
    hindsight  the best possible: the windiest half-hours that actually happened
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

import pandas as pd

from . import analysis as an
from . import outlook as ol
from .flex import SLOT, FlexJob, plan_load, score_against_actuals
from .store import WindData

STRATEGIES = ("plug_in", "timer", "once_raw", "once", "replan", "hindsight")


@dataclass(frozen=True)
class Night:
    energy_kwh: float = 21.0
    power_kw: float = 7.0
    plug_in: time = time(18, 0)
    deadline: time = time(7, 0)
    timer_start: time = time(0, 0)

    def job(self, day: date) -> FlexJob:
        start = an.uk_moment(day, self.plug_in)
        end = an.uk_moment(day + timedelta(days=1), self.deadline)
        return FlexJob(self.energy_kwh, self.power_kw, start, end)


def _hours(job: FlexJob, now: pd.Timestamp) -> int:
    return int((job.deadline - now.floor("h")) / an.HOUR) + 1


def _once(data: WindData, job: FlexJob, basis: str) -> pd.DataFrame:
    forecast, _ = ol.outlook(data, job.earliest, _hours(job, job.earliest))
    return plan_load(forecast, job, basis).slots


def _energies(job: FlexJob) -> list[float]:
    """Energy per half-hour at full power, the last one partial."""
    n = int(-(-job.energy_kwh // job.slot_kwh))
    return [job.slot_kwh] * (n - 1) + [job.energy_kwh - job.slot_kwh * (n - 1)] if n else []


def _first_slots(job: FlexJob, start: pd.Timestamp) -> pd.DataFrame:
    """Charge flat out from `start`."""
    energies = _energies(job)
    times = pd.date_range(start.ceil("30min"), periods=len(energies), freq=SLOT)
    return pd.DataFrame({"start_time": times.as_unit("ns"), "energy_kwh": energies})


def replan(data: WindData, job: FlexJob, basis: str = "expected") -> pd.DataFrame:
    """Plan at plug-in; at each new forecast run, keep what has already run and re-plan the
    rest. Returns the half-hours actually used (start_time, energy_kwh)."""
    runs = data.forecasts["publish_time"]
    moments = [job.earliest] + sorted(
        runs[(runs > job.earliest) & (runs < job.deadline)].drop_duplicates()
    )
    done: list[pd.DataFrame] = []
    remaining = job.energy_kwh
    for i, now in enumerate(moments):
        if remaining <= 1e-9:
            break
        until = moments[i + 1] if i + 1 < len(moments) else job.deadline
        forecast, _ = ol.outlook(data, now, _hours(job, now))
        step = FlexJob(remaining, job.power_kw, now, job.deadline, job.interruptible)
        slots = plan_load(forecast, step, basis).slots
        ran = slots[(slots["start_time"] < until) & (slots["energy_kwh"] > 0)]
        done.append(ran[["start_time", "energy_kwh"]])
        remaining -= float(ran["energy_kwh"].sum())
    if not done:
        empty = pd.Series(dtype="datetime64[ns, UTC]")
        return pd.DataFrame({"start_time": empty, "energy_kwh": pd.Series(dtype=float)})
    return pd.concat(done, ignore_index=True)


def hindsight(data: WindData, job: FlexJob) -> pd.DataFrame:
    """The windiest half-hours that actually happened (the best possible, for reference)."""
    window = data.actuals[
        (data.actuals["start_time"] >= job.earliest) & (data.actuals["start_time"] < job.deadline)
    ]
    energies = _energies(job)  # the weakest of the chosen half-hours takes the partial one
    best = window.nlargest(len(energies), "generation_mw")
    return pd.DataFrame({"start_time": best["start_time"].to_numpy(), "energy_kwh": energies})


def night(data: WindData, day: date, setup: Night | None = None) -> dict:
    """Actual wind (MW, energy-weighted) while charging, for each strategy, on one night."""
    setup = setup or Night()
    job = setup.job(day)
    timer = an.uk_moment(
        day + timedelta(days=1) if setup.timer_start < setup.plug_in else day, setup.timer_start
    )
    used = {
        "plug_in": _first_slots(job, job.earliest),
        "timer": _first_slots(job, timer),
        "once_raw": _once(data, job, "raw"),
        "once": _once(data, job, "expected"),
        "replan": replan(data, job),
        "hindsight": hindsight(data, job),
    }
    row = {"night": day}
    for name, slots in used.items():
        on = slots[slots["energy_kwh"] > 0]
        complete = abs(float(on["energy_kwh"].sum()) - job.energy_kwh) < 1e-6
        row[name] = score_against_actuals(on, data.actuals) if complete else None
    return row


def run(data: WindData, days: list[date], setup: Night | None = None) -> pd.DataFrame:
    return pd.DataFrame([night(data, day, setup) for day in days]).set_index("night")


def summary(nights: pd.DataFrame) -> pd.DataFrame:
    """Average wind while charging, and how much of the possible gain each strategy caught."""
    nights = nights.dropna()
    base, best = nights["plug_in"], nights["hindsight"]
    possible = (best - base).sum()
    rows = {}
    for name in nights.columns:
        rows[name] = {
            "avg wind while charging (MW)": round(nights[name].mean()),
            "vs plug-in": f"{(nights[name].mean() / base.mean() - 1) * 100:+.1f}%",
            "share of possible gain": (
                f"{(nights[name] - base).sum() / possible * 100:.0f}%" if possible else "n/a"
            ),
            "nights better than plug-in": (
                f"{int((nights[name] > base + 1e-6).sum())}/{len(nights)}"
            ),
        }
    return pd.DataFrame(rows).T
