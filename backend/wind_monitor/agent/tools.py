"""The agent's tools: thin wrappers over the tested planner, run in-process.

Results are small and pre-digested: whole numbers, UK times, and every comparison the agent
might want to state (such as "% more wind than charging straight away") already worked out.
The model only ever quotes numbers and never does arithmetic, which is what makes its answers
checkable (see factcheck.py).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime, time

import pandas as pd

from .. import outlook as ol
from ..carbon import GB_REGION, REGIONS, CarbonForecast, CarbonUnavailableError, region_id
from ..clock import as_utc
from ..compute import KW_PER_GPU, LONDON, ComputeJob, ComputePlan, SitePlan, plan_compute
from ..compute import average_g as site_average_g
from ..compute import kg_co2 as site_kg_co2
from ..config import HISTORY_DAYS, UK_TZ
from ..flex import (
    DEFAULT_DEADLINE,
    MAX_PLAN_HOURS,
    FlexJob,
    Plan,
    blocks,
    next_uk_time,
    plan_load,
    wind_during,
)
from ..store import WindData

REGION_NAMES = list(REGIONS.values())

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_wind_outlook",
            "description": (
                "GB wind generation outlook for the coming hours: the newest forecast, the "
                "expected value after correcting for the forecast's track record, an 80% range, "
                "and how far the forecast can be trusted. Times are UK local time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_PLAN_HOURS,
                        "description": "How many hours ahead (default 24)",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_charge",
            "description": (
                "Schedule an EV charge (or any flexible load) into the windiest half-hours "
                "before a deadline. Returns the charging windows, the expected wind while "
                "charging, and the comparison with charging straight away."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "energy_kwh": {"type": "number", "description": "Energy needed, kWh"},
                    "power_kw": {"type": "number", "description": "Charger power, kW"},
                    "deadline": {
                        "type": "string",
                        "description": (
                            "Done by: 'HH:MM' (next time it is that UK time) or "
                            "'YYYY-MM-DDTHH:MM' UK time. Default: next 07:00."
                        ),
                    },
                    "earliest": {
                        "type": "string",
                        "description": "Not before (same formats). Default: now.",
                    },
                    "can_pause": {
                        "type": "boolean",
                        "description": "Can the load stop and restart (EV: yes). Default true.",
                    },
                    "basis": {
                        "type": "string",
                        "enum": ["expected", "cautious", "raw"],
                        "description": (
                            "expected (default): forecast corrected by its track record; "
                            "cautious: plan for the low-wind case; raw: forecast as published"
                        ),
                    },
                    "goal": {
                        "type": "string",
                        "enum": ["wind", "carbon"],
                        "description": (
                            "wind (default): the windiest hours; carbon: the lowest grid "
                            "carbon intensity (NESO forecast), if the user asks for cleanest"
                        ),
                    },
                },
                "required": ["energy_kwh", "power_kw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_carbon_outlook",
            "description": (
                "How clean GB electricity is (g CO2 per kWh, NESO's forecast): now and hour by "
                "hour ahead for Great Britain, the cleanest and dirtiest hours, and every "
                "region ranked right now with its share of wind. Times are UK local time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_PLAN_HOURS,
                        "description": "How many hours ahead (default 24)",
                    },
                    "region": {
                        "type": "string",
                        "enum": REGION_NAMES,
                        "description": "Also give this region's hour-by-hour forecast",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_compute_job",
            "description": (
                "Plan a flexible compute job (AI training, fine-tuning, batch inference, "
                "rendering) for the least CO2: picks the region and the half-hours before the "
                "deadline where the grid is cleanest, and compares it with starting straight "
                "away in London (where most UK data centres are). Returns the run windows, "
                "CO2 in kg and every comparison."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gpus": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "GPUs the job runs on at once (default 8)",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Run time on those GPUs, hours",
                    },
                    "gpu_hours": {
                        "type": "number",
                        "description": "Total GPU-hours, if given instead of hours",
                    },
                    "deadline": {
                        "type": "string",
                        "description": (
                            "Done by: 'HH:MM' (next time it is that UK time) or "
                            "'YYYY-MM-DDTHH:MM' UK time. Default: 24 hours from the start."
                        ),
                    },
                    "earliest": {
                        "type": "string",
                        "description": "Not before (same formats). Default: now.",
                    },
                    "can_pause": {
                        "type": "boolean",
                        "description": "Checkpointed, can stop and resume. Default true.",
                    },
                    "regions": {
                        "type": "array",
                        "items": {"type": "string", "enum": REGION_NAMES},
                        "description": "Regions the job may run in. Default: all 14.",
                    },
                    "baseline_region": {
                        "type": "string",
                        "enum": REGION_NAMES,
                        "description": "Where it would run by default (default London)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_recent_wind",
            "description": (
                "How actual GB wind generation has compared with the forecast over the last "
                "few hours: is wind coming in below or above forecast right now?"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 24,
                        "description": "How many hours back (default 6)",
                    }
                },
            },
        },
    },
]


def uk_label(moment: pd.Timestamp, with_date: bool = False) -> str:
    """'Fri 18:00' (or 'Fri 30 Oct 18:00'): plans reach 48 hours, so the weekday is enough."""
    local = moment.tz_convert(UK_TZ)
    return local.strftime("%a %d %b %H:%M" if with_date else "%a %H:%M")


def _mw(value: float | None) -> int | None:
    return None if value is None or math.isnan(value) else int(round(value))


def parse_moment(text: str, now: pd.Timestamp) -> pd.Timestamp:
    """'HH:MM' -> the next time it is that UK time; 'YYYY-MM-DDTHH:MM' -> that UK time."""
    text = text.strip()
    try:
        if len(text) <= 5:
            return next_uk_time(now, time.fromisoformat(text))
        return as_utc(datetime.fromisoformat(text))
    except ValueError:
        raise ValueError(
            f"Could not read {text!r}: use 'HH:MM' or 'YYYY-MM-DDTHH:MM' (UK time)"
        ) from None


def _g(value: float | None) -> int | None:
    """g CO2/kWh as a whole number."""
    return None if value is None or math.isnan(value) else int(round(value))


def _kg(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(value, 2) if value < 10 else round(value, 1) if value < 1000 else round(value)


def _less_pct(new: float | None, old: float | None) -> float | None:
    """How much less `new` is than `old`, in % (negative if more)."""
    return None if new is None or not old else round((1 - new / old) * 100, 1)


def _windows(slots: pd.DataFrame) -> list[dict]:
    return [{"from": uk_label(b["start"]), "to": uk_label(b["end"])} for b in blocks(slots)]


class Toolbox:
    """Runs tool calls against the data as of `now`, and keeps a record of every call.

    `carbon` fetches the carbon intensity forecast when a tool first needs it (a download).
    """

    def __init__(
        self,
        data: WindData,
        now: pd.Timestamp,
        carbon: Callable[[], CarbonForecast] | None = None,
    ) -> None:
        self.data, self.now = data, now
        self._carbon_source = carbon
        self._carbon: CarbonForecast | None = None
        self.calls: list[dict] = []
        self.last_plan: Plan | None = None
        self.last_compute: ComputePlan | None = None
        self.last_kind: str | None = None  # "charge" or "compute": the newest good plan

    def carbon(self) -> CarbonForecast:
        if self._carbon is None:
            if self._carbon_source is None:
                raise ValueError("No carbon intensity forecast is available here")
            try:
                self._carbon = self._carbon_source()
            except CarbonUnavailableError as exc:
                raise ValueError(f"The carbon forecast is unavailable: {exc}") from None
        return self._carbon

    def run(self, name: str, arguments: str | dict | None) -> dict:
        """Run one call. Bad input comes back as {"error": ...} so the model can correct it."""
        try:
            args = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
            handler = {
                "get_wind_outlook": self.get_wind_outlook,
                "plan_charge": self.plan_charge,
                "check_recent_wind": self.check_recent_wind,
                "get_carbon_outlook": self.get_carbon_outlook,
                "plan_compute_job": self.plan_compute_job,
            }.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool {name!r}")
            result = handler(**(args or {}))
        except (ValueError, TypeError) as exc:
            result = {"error": str(exc)}
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result

    def _window(self, earliest: str | None, deadline: str | None, default_end):
        start = max(parse_moment(earliest, self.now), self.now) if earliest else self.now
        end = parse_moment(deadline, self.now) if deadline else default_end(start)
        if end <= start:
            raise ValueError("The deadline must be after the start")
        if end - self.now > pd.Timedelta(hours=MAX_PLAN_HOURS):
            raise ValueError(f"Plans can reach at most {MAX_PLAN_HOURS} hours ahead")
        return start, end

    # ---- tools --------------------------------------------------------------------------

    def get_wind_outlook(self, hours: int = 24) -> dict:
        hours = max(1, min(int(hours), MAX_PLAN_HOURS))
        forecast, history = ol.outlook(self.data, self.now, hours)
        published = ol.published_forecasts(self.data.forecasts, self.now)["publish_time"]
        rows = [
            {
                "time": uk_label(r.start_time),
                "forecast_mw": _mw(r.forecast_mw),
                "expected_mw": _mw(r.expected_mw),
                "low_mw": _mw(r.low_mw),
                "high_mw": _mw(r.high_mw),
            }
            for r in forecast.itertuples(index=False)
        ]
        ranked = [r for r in rows if r["expected_mw"] is not None]
        latest = ol.latest_actual(self.data, self.now)
        summary = ol.history_summary(history)
        return {
            "now": uk_label(self.now, with_date=True),
            "times": "UK local time",
            "newest_forecast_published": (
                uk_label(published.max(), with_date=True) if len(published) else None
            ),
            "latest_actual": (
                {"time": uk_label(pd.Timestamp(latest["time"])), "mw": _mw(latest["actual_mw"])}
                if latest
                else None
            ),
            "track_record": {
                "days": HISTORY_DAYS,
                "metered_wind_as_pct_of_forecast": {
                    row["lead_bucket"] + " ahead": _mw(row["actual_share_pct"]) for row in summary
                },
                "hours_forecast_too_high_pct": _mw(
                    (history["error_mw"] > 0).mean() * 100 if len(history) else None
                ),
            },
            "hours": rows,
            "windiest_hour": max(ranked, key=lambda r: r["expected_mw"]) if ranked else None,
            "calmest_hour": min(ranked, key=lambda r: r["expected_mw"]) if ranked else None,
            "note": "expected = forecast scaled by its track record; low-high = 80% range",
        }

    def plan_charge(
        self,
        energy_kwh: float,
        power_kw: float,
        deadline: str | None = None,
        earliest: str | None = None,
        can_pause: bool = True,
        basis: str = "expected",
        goal: str = "wind",
    ) -> dict:
        if goal not in ("wind", "carbon"):
            raise ValueError("goal must be 'wind' or 'carbon'")
        start, end = self._window(earliest, deadline, lambda s: next_uk_time(s, DEFAULT_DEADLINE))
        hours = math.ceil((end - self.now.floor("h")) / pd.Timedelta(hours=1))
        forecast, _ = ol.outlook(self.data, self.now, hours)
        job = FlexJob(float(energy_kwh), float(power_kw), start, end, bool(can_pause))
        try:  # carbon figures are a bonus for a wind plan, but a carbon plan needs them
            national = self.carbon().region(GB_REGION)
        except ValueError:
            if goal == "carbon":
                raise
            national = None
        plan = plan_load(forecast, job, "carbon" if goal == "carbon" else basis, national)
        self.last_plan, self.last_kind = plan, "charge"

        planned = wind_during(plan.slots, "expected_mw")
        asap = wind_during(plan.asap, "expected_mw")
        gain = (planned / asap - 1) * 100 if planned and asap else None
        result = {
            "feasible": plan.feasible,
            "energy_kwh": job.energy_kwh,
            "power_kw": job.power_kw,
            "earliest": uk_label(start),
            "deadline": uk_label(end),
            "charge_windows": [
                {
                    "from": uk_label(b["start"]),
                    "to": uk_label(b["end"]),
                    "kwh": round(b["energy_kwh"], 1),
                }
                for b in blocks(plan.slots)
            ],
            "hours_charging": round(float((plan.slots["energy_kwh"] > 0).sum()) / 2, 1),
            "expected_wind_while_charging_mw": _mw(planned),
            "low_case_wind_while_charging_mw": _mw(wind_during(plan.slots, "low_mw")),
            "if_charged_straight_away_mw": _mw(asap),
            "more_wind_than_charging_straight_away_pct": (None if gain is None else round(gain, 1)),
            "basis": basis,
            "goal": goal,
        }
        if national is not None:
            g_planned = wind_during(plan.slots, "carbon_g")
            g_asap = wind_during(plan.asap, "carbon_g")
            result |= {
                "grid_carbon_while_charging_g_per_kwh": _g(g_planned),
                "grid_carbon_if_charged_straight_away_g_per_kwh": _g(g_asap),
                "less_carbon_than_charging_straight_away_pct": _less_pct(g_planned, g_asap),
                "kg_co2": _kg(None if g_planned is None else g_planned * job.energy_kwh / 1000),
            }
        if not plan.feasible:
            result["shortfall_kwh"] = round(plan.shortfall_kwh, 1)
        return result

    def get_carbon_outlook(self, hours: int = 24, region: str | None = None) -> dict:
        hours = max(1, min(int(hours), MAX_PLAN_HOURS))
        forecast = self.carbon()
        rid = region_id(region) if region else None
        first, end = self.now.floor("h"), self.now + pd.Timedelta(hours=hours)

        def hourly(frame: pd.DataFrame) -> list[dict]:
            ahead = frame[(frame["start_time"] >= first) & (frame["start_time"] < end)]
            by_hour = ahead.groupby(ahead["start_time"].dt.floor("h"))["intensity_g"].mean()
            return [{"time": uk_label(t), "g_per_kwh": _g(v)} for t, v in by_hour.items()]

        rows = hourly(forecast.region(GB_REGION))
        ranked = [r for r in rows if r["g_per_kwh"] is not None]
        now_rows = forecast.at(self.now).sort_values(["intensity_g", "region_id"])
        regions_now = [
            {
                "region": r.region,
                "g_per_kwh": _g(r.intensity_g),
                "wind_pct": _g(getattr(r, "wind_pct", math.nan)),
            }
            for r in now_rows.itertuples(index=False)
        ]
        gb = forecast.national[forecast.national["start_time"] == self.now.floor("30min")]
        result = {
            "now": uk_label(self.now, with_date=True),
            "times": "UK local time",
            "source": "NESO Carbon Intensity API forecast (g CO2 per kWh)",
            "gb_now_g_per_kwh": _g(gb["intensity_g"].iloc[0]) if len(gb) else None,
            "gb_hours": rows,
            "gb_cleanest_hour": min(ranked, key=lambda r: r["g_per_kwh"]) if ranked else None,
            "gb_dirtiest_hour": max(ranked, key=lambda r: r["g_per_kwh"]) if ranked else None,
            "regions_now_cleanest_first": regions_now,
        }
        if regions_now:
            result["cleanest_region_now"] = regions_now[0]["region"]
            result["dirtiest_region_now"] = regions_now[-1]["region"]
        if rid is not None:
            result["region"] = REGIONS[rid]
            result["region_hours"] = hourly(forecast.region(rid))
        return result

    def plan_compute_job(
        self,
        gpus: int = 8,
        hours: float | None = None,
        gpu_hours: float | None = None,
        deadline: str | None = None,
        earliest: str | None = None,
        can_pause: bool = True,
        regions: list[str] | None = None,
        baseline_region: str | None = None,
    ) -> dict:
        gpus = int(gpus)
        if hours is None and gpu_hours is None:
            raise ValueError("Give hours (run time) or gpu_hours")
        if hours is None:
            hours = float(gpu_hours) / max(gpus, 1)
        start, end = self._window(earliest, deadline, lambda s: s + pd.Timedelta(hours=24))
        job = ComputeJob(gpus, float(hours), start, end, bool(can_pause))
        candidates = [region_id(r) for r in regions] if regions else None
        baseline = region_id(baseline_region) if baseline_region else LONDON
        plan = plan_compute(self.carbon(), job, candidates, baseline)
        best, base = plan.best, plan.baseline

        def site(s: SitePlan, slots: pd.DataFrame) -> dict:
            return {
                "region": s.region,
                "run_windows": _windows(slots),
                "average_g_per_kwh": _g(site_average_g(slots)),
                "kg_co2": _kg(site_kg_co2(slots)),
            }

        result = {
            "feasible": plan.feasible,
            "gpus": gpus,
            "run_hours": round(job.hours, 2),
            "gpu_hours": round(gpus * job.hours, 1),
            "kw_per_gpu_assumed": KW_PER_GPU,
            "power_kw": job.power_kw,
            "energy_kwh": job.energy_kwh,
            "earliest": uk_label(start),
            "deadline": uk_label(end),
            "baseline": {"what": f"start straight away in {base.region}", **site(base, base.asap)},
        }
        if best is None:
            result["why_not"] = "No region can fit the job before the deadline"
            return result
        self.last_compute, self.last_kind = plan, "compute"
        best_kg, base_kg = best.kg_co2, base.asap_kg_co2
        saved = None if best_kg is None or base_kg is None else base_kg - best_kg
        result |= {
            "best": site(best, best.slots),
            "pct_less_co2_than_baseline": _less_pct(best_kg, base_kg),
            "kg_co2_saved": _kg(saved),
            "only_moving_in_time": {
                "what": f"{base.region} at its cleanest time",
                **site(base, base.slots),
                "pct_less_co2_than_baseline": _less_pct(base.kg_co2, base_kg),
            },
            "only_moving_in_place": {
                "what": f"{best.region} straight away",
                **site(best, best.asap),
                "pct_less_co2_than_baseline": _less_pct(best.asap_kg_co2, base_kg),
            },
            "regions_least_co2_first": [
                {
                    "region": s.region,
                    "kg_co2": _kg(s.kg_co2),
                    "average_g_per_kwh": _g(site_average_g(s.slots)),
                }
                for s in plan.sites
                if s.feasible
            ],
        }
        return result

    def check_recent_wind(self, hours: float = 6) -> dict:
        hours = max(1.0, min(float(hours), 24.0))
        tracking = ol.recent_tracking(self.data, self.now, hours)
        latest = ol.latest_actual(self.data, self.now)
        if tracking.empty:
            return {"hours": hours, "hours_compared": 0, "verdict": "no recent actuals yet"}
        gap = tracking["error_mw"].mean()
        share = tracking["actual_mw"].sum() / tracking["forecast_mw"].sum() * 100
        if abs(share - 100) < 5:
            verdict = "wind is close to forecast"
        else:
            verdict = f"wind is running {'below' if gap > 0 else 'above'} forecast"
        return {
            "hours": hours,
            "hours_compared": len(tracking),
            "average_forecast_minus_actual_mw": _mw(gap),
            "actual_as_pct_of_forecast": _mw(share),
            "verdict": verdict,
            "latest_actual": (
                {"time": uk_label(pd.Timestamp(latest["time"])), "mw": _mw(latest["actual_mw"])}
                if latest
                else None
            ),
            "points": [
                {
                    "time": uk_label(r.start_time),
                    "actual_mw": _mw(r.actual_mw),
                    "forecast_mw": _mw(r.forecast_mw),
                }
                for r in tracking.itertuples(index=False)
            ],
        }
