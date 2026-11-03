"""Where and when to run a flexible compute job (AI training, batch inference) on the GB grid.

A data centre can't follow the wind, but the jobs inside one can. A checkpointed training run or
an overnight batch only has to finish by a deadline: it can wait for a cleaner hour, and if the
operator has capacity in more than one place, it can run where the grid is cleanest. On a windy
night that is a 0 g/kWh Scottish region instead of 200 g/kWh in London, where most UK data
centres are.

For each candidate region the job goes into that region's cleanest half-hours before the
deadline (or its cleanest unbroken run if it can't pause), and the region with the least CO2
wins. The baseline is the same job started straight away in London. Two more comparisons show
where the saving comes from: London at its cleanest time (moving in time only), and the winning
region straight away (moving in place only).

Power is estimated from the GPU count: an 8-GPU H100 server draws up to about 10.2 kW, so
1.3 kW per GPU with its share of the server, before cooling.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .carbon import REGIONS, CarbonForecast
from .flex import EPS_KWH, FlexJob, allocate

KW_PER_GPU = 1.3
LONDON = 13
MAX_GPUS = 100_000


@dataclass(frozen=True)
class ComputeJob:
    gpus: int
    hours: float  # run time on those GPUs
    earliest: pd.Timestamp
    deadline: pd.Timestamp
    interruptible: bool = True  # checkpointed: can pause and resume

    def __post_init__(self) -> None:
        if not 1 <= self.gpus <= MAX_GPUS:
            raise ValueError(f"gpus must be between 1 and {MAX_GPUS:,}")
        if not self.hours > 0:
            raise ValueError("hours must be more than zero")

    @property
    def power_kw(self) -> float:
        return round(self.gpus * KW_PER_GPU, 3)

    @property
    def energy_kwh(self) -> float:
        return round(self.power_kw * self.hours, 3)

    def as_flex(self) -> FlexJob:
        return FlexJob(
            self.energy_kwh, self.power_kw, self.earliest, self.deadline, self.interruptible
        )


@dataclass(frozen=True)
class SitePlan:
    region_id: int
    # Half-hours from earliest to deadline: start_time, carbon_g, energy_kwh (0 when off).
    slots: pd.DataFrame
    asap: pd.DataFrame  # the same energy started straight away
    energy_kwh: float  # what the job needs

    @property
    def region(self) -> str:
        return REGIONS[self.region_id]

    @property
    def feasible(self) -> bool:
        return float(self.slots["energy_kwh"].sum()) >= self.energy_kwh - 1e-6

    @property
    def kg_co2(self) -> float | None:
        return kg_co2(self.slots)

    @property
    def asap_kg_co2(self) -> float | None:
        return kg_co2(self.asap)


def kg_co2(slots: pd.DataFrame) -> float | None:
    """CO2 of the energy used, in kg; None if any half-hour in use has no forecast."""
    on = slots[slots["energy_kwh"] > EPS_KWH]
    if on.empty or on["carbon_g"].isna().any():
        return None
    return float((on["energy_kwh"] * on["carbon_g"]).sum() / 1000)


def average_g(slots: pd.DataFrame) -> float | None:
    """Energy-weighted carbon intensity of the half-hours in use (g CO2/kWh)."""
    kg = kg_co2(slots)
    used = float(slots["energy_kwh"].sum())
    return None if kg is None or used < EPS_KWH else kg * 1000 / used


def site_slots(forecast: CarbonForecast, rid: int, job: ComputeJob) -> pd.DataFrame:
    start, end = job.earliest.ceil("30min"), job.deadline.floor("30min")
    times = pd.date_range(start, end, freq="30min", inclusive="left") if end > start else []
    slots = pd.DataFrame({"start_time": pd.DatetimeIndex(times, tz="UTC").as_unit("ns")})
    values = forecast.region(rid).rename(columns={"intensity_g": "carbon_g"})
    return slots.merge(values, on="start_time", how="left")


def plan_site(forecast: CarbonForecast, rid: int, job: ComputeJob) -> SitePlan:
    slots = site_slots(forecast, rid, job)
    planned, asap = allocate(job.as_flex(), -slots["carbon_g"])
    return SitePlan(
        rid, slots.assign(energy_kwh=planned), slots.assign(energy_kwh=asap), job.energy_kwh
    )


@dataclass(frozen=True)
class ComputePlan:
    job: ComputeJob
    sites: list[SitePlan]  # every candidate, least CO2 first (unknown last)
    baseline: SitePlan  # the baseline region (its `asap` is the baseline)

    @property
    def best(self) -> SitePlan | None:
        ok = [s for s in self.sites if s.feasible and s.kg_co2 is not None]
        return ok[0] if ok else None

    @property
    def feasible(self) -> bool:
        return self.best is not None


def plan_compute(
    forecast: CarbonForecast,
    job: ComputeJob,
    regions: list[int] | None = None,
    baseline: int = LONDON,
) -> ComputePlan:
    candidates = list(dict.fromkeys(regions or REGIONS))
    sites = [plan_site(forecast, rid, job) for rid in candidates]
    sites.sort(key=lambda s: (s.kg_co2 is None or not s.feasible, s.kg_co2 or 0.0, s.region_id))
    base = next((s for s in sites if s.region_id == baseline), None)
    return ComputePlan(job, sites, base or plan_site(forecast, baseline, job))
