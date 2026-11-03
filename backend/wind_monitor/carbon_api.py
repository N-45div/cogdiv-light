"""HTTP routes for the grid's carbon intensity and for planning compute jobs (see compute.py)."""

from __future__ import annotations

import math
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from .brief import BriefDesk, get_brief_desk
from .carbon import REGIONS, CarbonFeed, CarbonForecast, CarbonUnavailableError, get_carbon_feed
from .clock import as_utc
from .compute import KW_PER_GPU, ComputeJob, SitePlan, average_g, kg_co2, plan_compute
from .flex import MAX_PLAN_HOURS, blocks
from .planning_api import ClockDep
from .schemas import BriefOut, CarbonView, ComputePlanView, ComputeRequest

router = APIRouter(prefix="/api")
CarbonFeedDep = Annotated[CarbonFeed, Depends(get_carbon_feed)]
BriefDeskDep = Annotated[BriefDesk, Depends(get_brief_desk)]
SOURCE = "NESO Carbon Intensity API (api.carbonintensity.org.uk), regional 48-hour forecast"


def _num(value: float) -> float | None:
    return None if math.isnan(value) else value


def forecast_now(feed: CarbonFeed, now: pd.Timestamp) -> CarbonForecast:
    try:
        return feed.get(now)
    except CarbonUnavailableError as exc:
        raise HTTPException(503, f"The carbon forecast is unavailable: {exc}") from exc


@router.get("/carbon", response_model=CarbonView)
def carbon_view(
    feed: CarbonFeedDep,
    clock: ClockDep,
    hours: Annotated[int, Query(ge=1, le=MAX_PLAN_HOURS)] = 24,
) -> dict:
    """How clean the grid is now and over the coming hours: GB and each of the 14 regions."""
    now = clock.now()
    forecast = forecast_now(feed, now)
    times = pd.DatetimeIndex(
        pd.date_range(now.floor("30min"), periods=hours * 2, freq="30min")
    ).as_unit("ns")

    def series(frame: pd.DataFrame) -> list[float | None]:
        values = frame.set_index("start_time")["intensity_g"].reindex(times)
        return [_num(float(v)) for v in values]

    current = forecast.at(now).set_index("region_id")
    regions = []
    for rid, name in REGIONS.items():
        row = current.loc[rid] if rid in current.index else None
        mix = {}
        if row is not None:
            for col in current.columns:
                if col.endswith("_pct") and not pd.isna(row[col]):
                    mix[col.removesuffix("_pct")] = float(row[col])
        regions.append(
            {
                "id": rid,
                "name": name,
                "now_g": None if row is None else _num(float(row["intensity_g"])),
                "index": None if row is None or pd.isna(row.get("index")) else row["index"],
                "mix": mix,
                "g": series(forecast.region(rid)),
            }
        )
    regions.sort(key=lambda r: (r["now_g"] is None, r["now_g"] or 0, r["id"]))
    national = series(forecast.national)
    return {
        "as_of": now.to_pydatetime(),
        "fetched_at": forecast.fetched_at.to_pydatetime(),
        "source": SOURCE,
        "times": [t.to_pydatetime() for t in times],
        "national": national,
        "gb_now_g": national[0] if national else None,
        "regions": regions,
    }


def _site(site: SitePlan, slots: pd.DataFrame) -> dict:
    kg, g = kg_co2(slots), average_g(slots)
    return {
        "region_id": site.region_id,
        "region": site.region,
        "feasible": site.feasible,
        "windows": [
            {**b, "start": b["start"].to_pydatetime(), "end": b["end"].to_pydatetime()}
            for b in blocks(slots)
        ],
        "average_g": None if g is None else round(g, 1),
        "kg_co2": None if kg is None else round(kg, 3),
    }


@router.post("/compute/plan", response_model=ComputePlanView)
def compute_plan(request: ComputeRequest, feed: CarbonFeedDep, clock: ClockDep) -> dict:
    """Where and when to run a flexible compute job for the least CO2, compared with starting it
    straight away in London. The planner alone: no model, nothing sent to a device."""
    now = clock.now()
    earliest = max(as_utc(request.earliest), now) if request.earliest else now
    deadline = as_utc(request.deadline) if request.deadline else earliest + pd.Timedelta(hours=24)
    if deadline <= earliest:
        raise HTTPException(422, "deadline must be after the start (and after now)")
    if deadline - now > pd.Timedelta(hours=MAX_PLAN_HOURS):
        raise HTTPException(422, f"Plans can reach at most {MAX_PLAN_HOURS} hours ahead")
    bad = [r for r in request.regions or [] if r not in REGIONS]
    if bad:
        raise HTTPException(422, f"Unknown region ids: {bad} (use 1-14)")
    job = ComputeJob(request.gpus, request.hours, earliest, deadline, request.interruptible)
    plan = plan_compute(forecast_now(feed, now), job, request.regions, request.baseline)
    best, base = plan.best, plan.baseline
    base_kg = base.asap_kg_co2

    def less(kg: float | None) -> float | None:
        return None if kg is None or not base_kg else round((1 - kg / base_kg) * 100, 1)

    saved = None
    if best and base_kg is not None and best.kg_co2 is not None:
        saved = round(base_kg - best.kg_co2, 3)
    return {
        "as_of": now.to_pydatetime(),
        "job": {
            "gpus": job.gpus,
            "hours": job.hours,
            "kw_per_gpu": KW_PER_GPU,
            "power_kw": job.power_kw,
            "energy_kwh": job.energy_kwh,
            "earliest": earliest.to_pydatetime(),
            "deadline": deadline.to_pydatetime(),
            "interruptible": job.interruptible,
        },
        "feasible": plan.feasible,
        "best": _site(best, best.slots) if best else None,
        "baseline": _site(base, base.asap),
        "time_only": _site(base, base.slots),
        "place_only": _site(best, best.asap) if best else None,
        "pct_less_co2": less(best.kg_co2) if best else None,
        "kg_co2_saved": saved,
        "sites": [_site(s, s.slots) for s in plan.sites],
    }


@router.get("/brief", response_model=BriefOut)
def grid_brief(desk: BriefDeskDep, feed: CarbonFeedDep, clock: ClockDep) -> dict:
    """The live note on where and when the grid is cleanest: written each half-hour by the
    open-weight watcher model and fact-checked, or by code if that fails."""
    try:
        return desk.quick(clock.now(), feed).as_dict()
    except CarbonUnavailableError as exc:
        raise HTTPException(503, f"The carbon forecast is unavailable: {exc}") from exc
