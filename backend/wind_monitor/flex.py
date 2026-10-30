"""Schedule flexible demand (an EV charge, a water heater) into the windiest half-hours.

The agent decides what to ask for and explains the result; the arithmetic lives here, as plain
functions that are easy to test. A job needs `energy_kwh` at up to `power_kw`, somewhere
between `earliest` and `deadline`. Each half-hour slot is scored by the wind expected on the
grid in that hour, and the job goes into the best slots:

- interruptible jobs (EV charging) take the best slots wherever they are;
- other jobs (a wash cycle) take the best run of consecutive slots.

"Best" depends on the basis:
    raw       the forecast taken at face value
    expected  the forecast corrected for its typical bias (median past error)
    cautious  the low-wind case: wind there is a 90% chance of beating
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time, timedelta

import pandas as pd

from . import analysis as an
from . import config

# Plans reach at most this far ahead: about the horizon of a WINDFOR run.
MAX_PLAN_HOURS = 48
# Without a deadline, a job must be done by the next 07:00 UK time (an overnight charge).
DEFAULT_DEADLINE = time(7, 0)
SLOT = pd.Timedelta(minutes=30)
SLOT_HOURS = 0.5
BASIS_COLUMNS = {"raw": "forecast_mw", "expected": "expected_mw", "cautious": "low_mw"}
VALUE_COLUMNS = ["forecast_mw", "expected_mw", "low_mw", "high_mw"]
# Energies below this are rounding noise.
EPS_KWH = 1e-9


@dataclass(frozen=True)
class FlexJob:
    energy_kwh: float
    power_kw: float
    earliest: pd.Timestamp
    deadline: pd.Timestamp
    interruptible: bool = True

    def __post_init__(self) -> None:
        if not self.energy_kwh >= 0:
            raise ValueError("energy_kwh must be zero or more")
        if not self.power_kw > 0:
            raise ValueError("power_kw must be more than zero")

    @property
    def slot_kwh(self) -> float:
        return self.power_kw * SLOT_HOURS


@dataclass(frozen=True)
class Plan:
    job: FlexJob
    basis: str
    # Every slot in the window. Columns: start_time, forecast_mw, expected_mw, low_mw, high_mw,
    # score_mw (the value used to rank it), energy_kwh (0 when off).
    slots: pd.DataFrame
    # The same energy run straight away from `earliest`: what a plain timer would do.
    asap: pd.DataFrame

    @property
    def planned_kwh(self) -> float:
        return float(self.slots["energy_kwh"].sum())

    @property
    def shortfall_kwh(self) -> float:
        return max(self.job.energy_kwh - self.planned_kwh, 0.0)

    @property
    def feasible(self) -> bool:
        return self.shortfall_kwh < 1e-6


def next_uk_time(after: pd.Timestamp, at: time) -> pd.Timestamp:
    """The first time-of-day `at` (UK local) strictly after `after`."""
    day = after.tz_convert(config.UK_TZ).date()
    candidate = an.uk_moment(day, at)
    if candidate <= after:
        candidate = an.uk_moment(day + timedelta(days=1), at)
    return candidate


def slots_for(
    outlook: pd.DataFrame, earliest: pd.Timestamp, deadline: pd.Timestamp
) -> pd.DataFrame:
    """Half-hour slots from `earliest` (rounded up) to `deadline` (rounded down).

    Forecasts are hourly, so both half-hours of an hour get that hour's values. Slots beyond
    the forecast horizon get NaN.
    """
    start, end = earliest.ceil("30min"), deadline.floor("30min")
    times = pd.date_range(start, end, freq="30min", inclusive="left") if end > start else []
    slots = pd.DataFrame({"start_time": pd.DatetimeIndex(times, tz="UTC").as_unit("ns")})
    slots["hour"] = slots["start_time"].dt.floor("h")
    available = [c for c in VALUE_COLUMNS if c in outlook.columns]
    values = outlook[["start_time", *available]].rename(columns={"start_time": "hour"})
    slots = slots.merge(values, on="hour", how="left").drop(columns="hour")
    for col in VALUE_COLUMNS:
        if col not in slots:
            slots[col] = math.nan
    return slots


def _slot_energies(job: FlexJob, count: int) -> list[float]:
    """Energy for each slot used, best slot first: full slots, then any remainder."""
    if job.energy_kwh < EPS_KWH:
        return []
    full = math.floor(job.energy_kwh / job.slot_kwh + EPS_KWH)
    remainder = job.energy_kwh - full * job.slot_kwh
    energies = [job.slot_kwh] * full + ([remainder] if remainder > EPS_KWH else [])
    return energies[:count]  # not enough slots: the job cannot finish (see Plan.feasible)


def _fill_best(scores: pd.Series, energies: list[float]) -> list[float]:
    """Interruptible: best-scoring slots first (unknown scores last, earlier slots on ties)."""
    order = pd.DataFrame({"missing": scores.isna(), "score": -scores.fillna(0.0)})
    order["position"] = range(len(order))
    ranked = order.sort_values(["missing", "score", "position"])["position"].to_list()
    out = [0.0] * len(scores)
    for position, energy in zip(ranked, energies, strict=False):
        out[position] = energy
    return out


def _fill_consecutive(scores: pd.Series, energies: list[float]) -> list[float]:
    """Not interruptible: the run of consecutive slots with the most wind per kWh."""
    n = len(energies)
    out = [0.0] * len(scores)
    if n == 0:
        return out
    values = scores.to_list()
    best_key, best_start = None, 0
    for start in range(len(values) - n + 1):
        window = values[start : start + n]
        missing = sum(1 for v in window if math.isnan(v))
        wind = sum(e * v for e, v in zip(energies, window, strict=True) if not math.isnan(v))
        key = (missing, -wind, start)
        if best_key is None or key < best_key:
            best_key, best_start = key, start
    out[best_start : best_start + n] = energies
    return out


def plan_load(outlook: pd.DataFrame, job: FlexJob, basis: str = "expected") -> Plan:
    """Put the job in the windiest slots. Where the chosen basis has no value (no error history
    yet), the raw forecast is used for that slot."""
    if basis not in BASIS_COLUMNS:
        raise ValueError(f"basis must be one of {sorted(BASIS_COLUMNS)}")
    slots = slots_for(outlook, job.earliest, job.deadline)
    slots["score_mw"] = slots[BASIS_COLUMNS[basis]].fillna(slots["forecast_mw"])
    energies = _slot_energies(job, len(slots))
    fill = _fill_best if job.interruptible else _fill_consecutive
    planned = slots.assign(energy_kwh=fill(slots["score_mw"], energies))
    asap = slots.assign(energy_kwh=energies + [0.0] * (len(slots) - len(energies)))
    return Plan(job, basis, planned, asap)


# ---- describing a plan ---------------------------------------------------------------


def blocks(slots: pd.DataFrame) -> list[dict]:
    """Runs of consecutive "on" slots, e.g. [{start: 01:00, end: 03:00, energy_kwh: 14}]."""
    on = slots[slots["energy_kwh"] > EPS_KWH].sort_values("start_time")
    out: list[dict] = []
    for row in on.itertuples(index=False):
        if out and out[-1]["end"] == row.start_time:
            out[-1]["end"] = row.start_time + SLOT
            out[-1]["energy_kwh"] += row.energy_kwh
        else:
            end = row.start_time + SLOT
            out.append({"start": row.start_time, "end": end, "energy_kwh": row.energy_kwh})
    return out


def wind_during(slots: pd.DataFrame, column: str) -> float | None:
    """Average of `column` over the slots in use, weighted by the energy used in each.

    None if nothing runs or any slot in use has no value: an average over part of the run
    would be misleading.
    """
    on = slots[slots["energy_kwh"] > EPS_KWH]
    if on.empty or on[column].isna().any():
        return None
    return float((on[column] * on["energy_kwh"]).sum() / on["energy_kwh"].sum())


def score_against_actuals(slots: pd.DataFrame, actuals: pd.DataFrame) -> float | None:
    """Average ACTUAL wind output while the job ran (energy-weighted): the after-the-fact score.

    Actuals are half-hourly, so each slot has its own value. None if any is missing.
    """
    joined = slots.merge(
        actuals.rename(columns={"generation_mw": "actual_mw"}), on="start_time", how="left"
    )
    return wind_during(joined, "actual_mw")
