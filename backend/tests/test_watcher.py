"""The watcher: proposes a better window when the forecast changes its mind, alerts when wind
drops with nowhere better to go, and stays quiet when the plan is on track.

Nights of 4-5 Jan (GMT, so UK time = UTC). Charging 7 kWh at 7 kW = one hour.
"""

import json

import pandas as pd
import pytest
from test_agent_runner import FakeModel, say
from wind_fixtures import night_wind

from wind_monitor.agent.runner import AgentRun
from wind_monitor.agent.tools import Toolbox
from wind_monitor.device import DeviceRegistry, relay_on, screen
from wind_monitor.flex import blocks
from wind_monitor.watcher import Watcher, explain, remaining_slots

EVENING = pd.Timestamp("2026-01-04T18:00Z")
CHECK = pd.Timestamp("2026-01-04T20:00Z")  # just after the 20:00 run


def moved(early_hour, late_hour):
    """Forecast that puts the windy hour at early_hour until 20:00, then at late_hour."""

    def early(t):
        return 15_000.0 if t.hour == early_hour else 10_000.0

    def late(t):
        return 15_000.0 if t.hour == late_hour else 10_000.0

    return early, late


def approved_plan(data, registry, now=EVENING, **job) -> AgentRun:
    """Plan a charge with the real tool at `now`, as the agent would, and approve it."""
    toolbox = Toolbox(data, now)
    result = toolbox.plan_charge(**{"energy_kwh": 7, "power_kw": 7, "deadline": "07:00", **job})
    plan = toolbox.last_plan
    run = AgentRun(question="q", as_of=now.isoformat(), mode="replay", answer="ok", verified=True)
    run.factcheck = {"passed": True, "summary": "facts checked 3/3", "claims": []}
    run.plan = result
    run.schedule = [
        {
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "energy_kwh": b["energy_kwh"],
        }
        for b in blocks(plan.slots)
    ]
    run.job = {
        "energy_kwh": plan.job.energy_kwh,
        "power_kw": plan.job.power_kw,
        "earliest": plan.job.earliest.isoformat(),
        "deadline": plan.job.deadline.isoformat(),
        "interruptible": True,
    }
    registry.propose("desk", run, now)
    registry.event("desk", {"type": "approve", "proposal_id": run.id}, now)
    return run


def test_a_late_forecast_change_becomes_a_better_window_proposal():
    early, late = moved(22, 3)
    data = night_wind(early, late, actual=late)
    registry = DeviceRegistry()
    first = approved_plan(data, registry)
    assert first.schedule[0]["start"].startswith("2026-01-04T22:00")  # fooled at 18:00

    [finding] = Watcher().run(CHECK, registry, data, client=None)

    assert finding.outcome == "better_window"
    assert finding.facts["remaining_windows"] == ["Sun 22:00-23:00"]
    assert finding.facts["newest_expected_wind_for_those_windows_mw"] == 10_000
    assert finding.facts["better_windows"] == ["Mon 03:00-04:00"]
    assert finding.facts["more_wind_than_current_plan_pct"] == 50.0
    assert "Mon 03:00-04:00" in finding.message and finding.note is None

    state = registry.with_active_plans()[0]
    view = screen(state, CHECK, None)
    assert view["title"] == "Better window?" and "+50% vs old plan" in view["lines"]
    registry.event("desk", {"type": "approve", "proposal_id": finding.proposal_id}, CHECK)
    assert relay_on(state.active, pd.Timestamp("2026-01-05T03:30Z"))
    assert not relay_on(state.active, pd.Timestamp("2026-01-04T22:30Z"))


def test_wind_dropping_everywhere_shows_an_alert():
    data = night_wind(lambda t: 15_000.0, lambda t: 10_000.0, actual=lambda t: 10_000.0)
    registry = DeviceRegistry()
    approved_plan(data, registry, earliest="23:00")
    [finding] = Watcher().run(CHECK, registry, data, client=None)
    assert finding.outcome == "below_plan"
    assert finding.facts["change_since_planned_pct"] == pytest.approx(-33.3, abs=0.1)
    state = registry.with_active_plans()[0]
    assert state.alert == "Wind 33% below plan"
    assert "Wind 33% below plan" in screen(state, CHECK, None)["lines"]


def test_a_plan_on_track_stays_quiet_and_clears_old_alerts():
    early, late = moved(3, 3)
    data = night_wind(early, late, actual=late)
    registry = DeviceRegistry()
    approved_plan(data, registry)
    registry.set_alert("desk", "Wind 20% below plan", EVENING)
    [finding] = Watcher().run(CHECK, registry, data, client=None)
    assert finding.outcome == "on_track"
    assert registry.with_active_plans()[0].alert is None
    assert finding.message == "On track: 7 kWh still to charge in Mon 03:00-04:00."


def test_finished_plans_and_idle_devices_are_skipped():
    early, late = moved(3, 3)
    data = night_wind(early, late, actual=late)
    registry = DeviceRegistry()
    approved_plan(data, registry)
    findings = Watcher().run(pd.Timestamp("2026-01-05T06:00Z"), registry, data, client=None)
    assert [f.outcome for f in findings] == ["done"]


def test_runs_every_30_minutes_on_the_planning_clock():
    watcher = Watcher()
    assert watcher.due(CHECK)
    watcher.last = CHECK
    assert not watcher.due(CHECK + pd.Timedelta(minutes=29))
    assert watcher.due(CHECK + pd.Timedelta(minutes=30))
    assert watcher.due(CHECK - pd.Timedelta(hours=5))  # replay jumped back


def test_remaining_slots_skip_what_has_started():
    window = {"start": "2026-01-04T22:00:00+00:00", "end": "2026-01-04T23:00:00+00:00"}
    schedule = [{**window, "energy_kwh": 7.0}]
    left = remaining_slots(schedule, pd.Timestamp("2026-01-04T22:30Z"))
    assert list(left["start_time"].dt.strftime("%H:%M")) == ["22:30"]
    assert list(left["energy_kwh"]) == [3.5]


FACTS = {
    "better_windows": ["Mon 03:00-04:00"],
    "better_expected_wind_mw": 15000,
    "more_wind_than_current_plan_pct": 50.0,
    "checked_at": "Sun 04 Jan 20:00",
}


class Watch(FakeModel):
    def available(self):
        return ["fake:watcher"]


def test_the_watcher_models_note_is_fact_checked():
    good = "Moving your charge to 03:00-04:00 should catch about 15,000 MW, 50% more wind."
    note, check, _ = explain(FACTS, "better_window", Watch(say(good)))
    assert note == good and check.startswith("facts checked")
    wrong = say("Moving to 03:00 gives 90% more wind.")
    note, check, _ = explain(FACTS, "better_window", Watch(wrong, wrong))
    assert note is None and "fact check" in check
    assert explain(FACTS, "better_window", None)[0] is None


def test_the_model_sees_the_facts():
    model = Watch(say("Moving to 03:00-04:00 gets 50% more wind."))
    explain(FACTS, "better_window", model)
    assert json.loads(model.sent[0][1]["content"])["more_wind_than_current_plan_pct"] == 50.0
