"""The agent's tools: thin wrappers over the tested planner, run in-process.

Results are small and pre-digested: whole numbers, UK times, and every comparison the agent
might want to state (such as "% more wind than charging straight away") already worked out.
The model only ever quotes numbers and never does arithmetic, which is what makes its answers
checkable (see factcheck.py).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, time

import pandas as pd

from .. import outlook as ol
from ..clock import as_utc
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
                },
                "required": ["energy_kwh", "power_kw"],
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


class Toolbox:
    """Runs tool calls against the data as of `now`, and keeps a record of every call."""

    def __init__(self, data: WindData, now: pd.Timestamp) -> None:
        self.data, self.now = data, now
        self.calls: list[dict] = []
        self.last_plan: Plan | None = None

    def run(self, name: str, arguments: str | dict | None) -> dict:
        """Run one call. Bad input comes back as {"error": ...} so the model can correct it."""
        try:
            args = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
            handler = {
                "get_wind_outlook": self.get_wind_outlook,
                "plan_charge": self.plan_charge,
                "check_recent_wind": self.check_recent_wind,
            }.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool {name!r}")
            result = handler(**(args or {}))
        except (ValueError, TypeError) as exc:
            result = {"error": str(exc)}
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result

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
    ) -> dict:
        start = max(parse_moment(earliest, self.now), self.now) if earliest else self.now
        if deadline:
            end = parse_moment(deadline, self.now)
        else:
            end = next_uk_time(start, DEFAULT_DEADLINE)
        if end <= start:
            raise ValueError("The deadline must be after the start")
        if end - self.now > pd.Timedelta(hours=MAX_PLAN_HOURS):
            raise ValueError(f"Plans can reach at most {MAX_PLAN_HOURS} hours ahead")
        hours = math.ceil((end - self.now.floor("h")) / pd.Timedelta(hours=1))
        forecast, _ = ol.outlook(self.data, self.now, hours)
        job = FlexJob(float(energy_kwh), float(power_kw), start, end, bool(can_pause))
        plan = plan_load(forecast, job, basis)
        self.last_plan = plan

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
        }
        if not plan.feasible:
            result["shortfall_kwh"] = round(plan.shortfall_kwh, 1)
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
