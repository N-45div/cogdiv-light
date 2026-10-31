"""The watcher: re-checks every approved plan as new forecasts and actuals arrive.

Every 30 minutes on the planning clock (so it also runs on simulated time in replay), for each
device whose approved plan hasn't finished:

1. Re-plan the energy still to come with the newest forecast.
2. If that would get at least 5% more wind than the plan's remaining windows now look like
   getting, propose it to the device. The person approves with the button, as with any plan.
3. Otherwise, if the newest forecast for the plan's remaining windows is 15% or more below what
   the plan expected, show an alert line on the device.
4. Otherwise, the plan is on track.

The numbers come from the tested planner. A watcher model (Muse Glimmer on our own Modal GPU, or
Muse Spark Open on Baseten) only writes a one-line explanation, which is fact-checked like the
agent's answers. If that fails, or no model is set up, a plain sentence written by code is used.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field

import pandas as pd

from . import outlook as ol
from .agent import factcheck
from .agent.llm import ChatClient, ModelUnavailableError
from .agent.runner import AgentRun
from .agent.tools import uk_label
from .device import DeviceRegistry, DeviceState
from .flex import SLOT, FlexJob, blocks, plan_load, slots_for, wind_during
from .store import WindData

log = logging.getLogger(__name__)

WATCH_EVERY = pd.Timedelta(minutes=30)
BETTER_BY = 0.05  # propose a new plan if it catches this much more wind
BELOW_BY = -0.15  # alert if the remaining windows now look this much less windy than planned

WATCH_PROMPT = """\
You are the watcher for a household's wind-aware EV charging plan in Great Britain. You get the \
facts of your latest check as JSON. Write one or two short sentences for the person: what changed \
and what you suggest. Quote figures exactly as given (you may round); never calculate new ones. \
Times are UK local time.
"""


@dataclass
class Finding:
    device_id: str
    checked_at: str
    outcome: str  # better_window, below_plan, on_track, no_forecast
    facts: dict
    message: str  # written by code
    note: str | None = None  # written by the watcher model, fact-checked
    note_check: str | None = None
    model: str | None = None
    proposal_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def as_dict(self) -> dict:
        return dict(vars(self))


def remaining_slots(schedule: list[dict], after: pd.Timestamp) -> pd.DataFrame:
    """The plan's half-hours that haven't started yet, with their energy."""
    rows = []
    for window in schedule:
        start, end = pd.Timestamp(window["start"]), pd.Timestamp(window["end"])
        n = int((end - start) / SLOT)
        for i in range(n):
            slot = start + i * SLOT
            if slot >= after:
                rows.append((slot, window["energy_kwh"] / n))
    times = pd.DatetimeIndex([r[0] for r in rows], tz="UTC").as_unit("ns")
    return pd.DataFrame({"start_time": times, "energy_kwh": [r[1] for r in rows]})


def _windows(slots: pd.DataFrame) -> list[str]:
    return [f"{uk_label(b['start'])}-{uk_label(b['end'])[-5:]}" for b in blocks(slots)]


def _pct(new: float, old: float) -> float:
    return round((new / old - 1) * 100, 1)


def check(state: DeviceState, data: WindData, now: pd.Timestamp) -> tuple[str, dict, object]:
    """Decide for one device. Returns (outcome, facts, new plan or None)."""
    plan = state.active
    job = plan.job or {}
    after = now.ceil("30min")
    left = remaining_slots(plan.schedule, after)
    if left.empty or not job:
        return "done", {}, None
    deadline = pd.Timestamp(job["deadline"])
    hours = math.ceil((deadline - now.floor("h")) / pd.Timedelta(hours=1))
    forecast, _ = ol.outlook(data, now, max(hours, 1))
    values = slots_for(forecast, after, deadline).drop(columns=["forecast_mw"], errors="ignore")
    left = left.merge(values, on="start_time", how="left")
    planned = plan.plan.get("expected_wind_while_charging_mw")
    now_expected = wind_during(left, "expected_mw")
    remaining_kwh = round(float(left["energy_kwh"].sum()), 3)
    facts = {
        "checked_at": uk_label(now, with_date=True),
        "remaining_kwh": remaining_kwh,
        "remaining_windows": _windows(left),
        "plan_expected_wind_mw": planned,
        "newest_expected_wind_for_those_windows_mw": (
            None if now_expected is None else int(round(now_expected))
        ),
    }
    if now_expected is None or not planned:
        return "no_forecast", facts, None
    facts["change_since_planned_pct"] = _pct(now_expected, planned)

    # Keep the person's own limits: not before their original start, done by their deadline.
    earliest = max(now, pd.Timestamp(job["earliest"])) if job.get("earliest") else now
    new_job = FlexJob(
        remaining_kwh, job["power_kw"], earliest, deadline, job.get("interruptible", True)
    )
    new_plan = plan_load(forecast, new_job, "expected")
    new_expected = wind_during(new_plan.slots, "expected_mw")
    if new_plan.feasible and new_expected and new_expected >= now_expected * (1 + BETTER_BY):
        facts["better_windows"] = _windows(new_plan.slots)
        facts["better_expected_wind_mw"] = int(round(new_expected))
        facts["more_wind_than_current_plan_pct"] = _pct(new_expected, now_expected)
        return "better_window", facts, new_plan
    if facts["change_since_planned_pct"] <= BELOW_BY * 100:
        return "below_plan", facts, None
    return "on_track", facts, None


def plain_message(outcome: str, facts: dict) -> str:
    """The code-written sentence: always correct, used if the model's isn't."""
    if outcome == "better_window":
        return (
            f"The newest forecast favours {', '.join(facts['better_windows'])}: about "
            f"{facts['better_expected_wind_mw']:,} MW expected, "
            f"{facts['more_wind_than_current_plan_pct']:g}% more wind than the current plan. "
            "Hold the button to switch."
        )
    if outcome == "below_plan":
        now_mw = facts["newest_expected_wind_for_those_windows_mw"]
        return (
            f"Wind for your charging windows now looks "
            f"{abs(facts['change_since_planned_pct']):g}% lower than planned "
            f"({now_mw:,} MW vs {facts['plan_expected_wind_mw']:,} MW), "
            "and no better window is left."
        )
    if outcome == "on_track":
        return (
            f"On track: {facts['remaining_kwh']:g} kWh still to charge in "
            f"{', '.join(facts['remaining_windows'])}."
        )
    return "No forecast for the remaining windows yet."


def explain(facts: dict, outcome: str, client: ChatClient | None) -> tuple[str | None, str, str]:
    """The watcher model's sentence, if it passes the fact check. Returns (note, check, model)."""
    if client is None or not client.available():
        return None, "no model", ""
    evidence = [{"name": "watch_check", "result": {**facts, "outcome": outcome}}]
    messages = [
        {"role": "system", "content": WATCH_PROMPT},
        {"role": "user", "content": json.dumps({**facts, "outcome": outcome})},
    ]
    try:
        for _ in range(2):
            result = client.chat(messages)
            note = (result.message.get("content") or "").strip()
            report = factcheck.check(note, evidence)
            if note and report.passed:
                return note, report.summary, result.model
            bad = ", ".join(f"'{c.text}'" for c in report.unsupported)
            messages += [
                {"role": "assistant", "content": note},
                {"role": "user", "content": f"These figures aren't in the facts: {bad}. Rewrite."},
            ]
    except ModelUnavailableError as exc:
        return None, f"model unavailable: {exc}", ""
    return None, "model's note failed the fact check", ""


def _proposal_run(device: str, finding_facts: dict, plan, message: str, job: dict) -> AgentRun:
    """Wrap a watcher re-plan as a verified run, so it reaches the device like any plan."""
    run = AgentRun(
        question=f"(watcher) re-check of {device}'s plan",
        as_of=finding_facts["checked_at"],
        mode="watcher",
        answer=message,
        verified=True,
        factcheck={"passed": True, "summary": "re-planned by the watcher", "claims": []},
    )
    run.plan = {
        "feasible": True,
        "expected_wind_while_charging_mw": finding_facts["better_expected_wind_mw"],
        "more_wind_than_current_plan_pct": finding_facts["more_wind_than_current_plan_pct"],
    }
    run.schedule = [
        {
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "energy_kwh": round(b["energy_kwh"], 3),
        }
        for b in blocks(plan.slots)
    ]
    run.job = {**job, "energy_kwh": plan.job.energy_kwh, "earliest": plan.job.earliest.isoformat()}
    return run


class Watcher:
    def __init__(self, every: pd.Timedelta = WATCH_EVERY, size: int = 200) -> None:
        self.every = every
        self.last: pd.Timestamp | None = None
        self.findings: deque[Finding] = deque(maxlen=size)
        self._lock = threading.Lock()

    def due(self, now: pd.Timestamp) -> bool:
        """Every `every` on the planning clock; also after the clock jumps back (replay)."""
        return self.last is None or now >= self.last + self.every or now < self.last

    def run(
        self,
        now: pd.Timestamp,
        registry: DeviceRegistry,
        data: WindData,
        client: ChatClient | None,
    ) -> list[Finding]:
        with self._lock:
            self.last = now
            found = []
            for state in registry.with_active_plans():
                try:
                    found.append(self._check_one(state, now, registry, data, client))
                except Exception:  # one bad device must not stop the others
                    log.exception("Watcher check failed for %s", state.device_id)
            for finding in found:
                self.findings.appendleft(finding)
            return found

    def _check_one(self, state, now, registry, data, client) -> Finding:
        outcome, facts, new_plan = check(state, data, now)
        message = plain_message(outcome, facts)
        finding = Finding(state.device_id, now.isoformat(), outcome, facts, message)
        if outcome in ("better_window", "below_plan"):
            finding.note, finding.note_check, finding.model = explain(facts, outcome, client)
        if outcome == "better_window":
            run = _proposal_run(
                state.device_id, facts, new_plan, finding.note or message, state.active.job
            )
            registry.propose(state.device_id, run, now, title="Better window?")
            finding.proposal_id = run.id
            registry.set_alert(state.device_id, None, now)
        elif outcome == "below_plan":
            change = abs(facts["change_since_planned_pct"])
            registry.set_alert(state.device_id, f"Wind {change:.0f}% below plan", now)
        elif outcome == "on_track":
            registry.set_alert(state.device_id, None, now)
        return finding

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [f.as_dict() for f in list(self.findings)[:limit]]


_watcher = Watcher()


def get_watcher() -> Watcher:
    return _watcher
