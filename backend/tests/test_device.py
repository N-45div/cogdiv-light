"""Device logic: proposals, the button, and a relay that follows the plan by the clock.

The plan: 14 kWh, 02:00-04:00 on Fri 9 Jan, ~15,000 MW expected, 50% more wind than charging
at plug-in. The clock starts at 18:00 on Thu 8 Jan (UK time = UTC in January).
"""

import pandas as pd
import pytest

from wind_monitor.agent.runner import AgentRun
from wind_monitor.device import (
    SCREEN_WIDTH,
    DeviceError,
    DeviceRegistry,
    Proposal,
    relay_on,
    screen,
)

NOW = pd.Timestamp("2026-01-08T18:00Z")
LATEST = {"time": "2026-01-08T17:00:00+00:00", "actual_mw": 10_000.0}
WINDOW = {
    "start": "2026-01-09T02:00:00+00:00",
    "end": "2026-01-09T04:00:00+00:00",
    "energy_kwh": 14.0,
}


def _run(run_id="run1", verified=True, feasible=True, schedule=None) -> AgentRun:
    return AgentRun(
        question="When should I charge?",
        as_of=NOW.isoformat(),
        mode="replay",
        id=run_id,
        answer="Charge 02:00-04:00.",
        verified=verified,
        factcheck={"summary": "facts checked 4/4", "passed": verified, "claims": []},
        plan={
            "feasible": feasible,
            "expected_wind_while_charging_mw": 15_000,
            "more_wind_than_charging_straight_away_pct": 50.0,
        },
        schedule=[WINDOW] if schedule is None else schedule,
    )


def _at(hhmm_day: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-01-{hhmm_day}Z")


@pytest.fixture
def registry() -> DeviceRegistry:
    return DeviceRegistry()


def test_idle_screen_shows_the_wind_now(registry):
    view = screen(registry.seen("desk", NOW), NOW, LATEST)
    assert view == {
        "mode": "idle",
        "title": "Wind now",
        "lines": ["10.0 GW", "at 17:00", "Ask me to plan"],
        "prompt": None,
    }


def test_a_verified_plan_becomes_a_proposal_on_screen(registry):
    state = registry.propose("desk", _run(), NOW)
    assert state.version == 1
    view = screen(state, NOW, LATEST)
    assert view["mode"] == "proposal" and view["title"] == "Charge plan?"
    assert view["lines"] == [
        "02:00-04:00 14kWh",
        "Wind ~15.0 GW",
        "+50% vs plug-in",
        "facts checked 4/4",
    ]
    assert view["prompt"] == "hold:yes  tap:no"


@pytest.mark.parametrize(
    ("run", "message"),
    [
        (_run(verified=False), "fact-checked"),
        (_run(feasible=False), "no feasible"),
        (_run(schedule=[]), "no feasible"),
    ],
)
def test_only_verified_feasible_plans_can_be_proposed(registry, run, message):
    with pytest.raises(ValueError, match=message):
        registry.propose("desk", run, NOW)


def test_approved_plan_drives_the_relay_by_the_clock(registry):
    registry.propose("desk", _run(), NOW)
    state = registry.event("desk", {"type": "approve", "proposal_id": "run1"}, NOW)
    assert state.proposal is None and state.active.id == "run1"

    timeline = {
        "08T18:00": ("waiting", False),
        "09T01:59": ("waiting", False),
        "09T02:00": ("charging", True),
        "09T03:59": ("charging", True),
        "09T04:00": ("done", False),
    }
    for moment, (mode, on) in timeline.items():
        assert relay_on(state.active, _at(moment)) is on, moment
        assert screen(state, _at(moment), LATEST)["mode"] == mode, moment
    assert screen(state, _at("09T02:30"), LATEST)["lines"][0] == "until 04:00"
    assert screen(state, _at("08T18:00"), LATEST)["lines"][0] == "Next 02:00-04:00"
    assert screen(state, _at("09T05:00"), LATEST)["lines"][0] == "14 kWh"


def test_reject_and_stop(registry):
    registry.propose("desk", _run(), NOW)
    state = registry.event("desk", {"type": "reject", "proposal_id": "run1"}, NOW)
    assert state.proposal is None and state.active is None
    registry.propose("desk", _run("run2"), NOW)
    registry.event("desk", {"type": "approve", "proposal_id": "run2"}, NOW)
    state = registry.event("desk", {"type": "stop"}, NOW)
    assert state.active is None and not relay_on(state.active, _at("09T02:30"))
    assert [e["event"] for e in state.events][:2] == ["stopped run2", "approved run2"]


def test_a_newer_proposal_replaces_a_waiting_one(registry):
    registry.propose("desk", _run("old"), NOW)
    registry.propose("desk", _run("new"), NOW)
    with pytest.raises(DeviceError):
        registry.event("desk", {"type": "approve", "proposal_id": "old"}, NOW)
    assert registry.event("desk", {"type": "approve", "proposal_id": "new"}, NOW).active.id == "new"


def test_telemetry_and_unknown_events(registry):
    state = registry.event("desk", {"type": "telemetry", "relay": False, "rssi": -61}, NOW)
    assert state.telemetry == {"relay": False, "rssi": -61} and state.last_seen == NOW.isoformat()
    with pytest.raises(DeviceError):
        registry.event("desk", {"type": "dance"}, NOW)


def test_lines_fit_the_screen_and_many_windows_are_summarised(registry):
    windows = [
        {
            "start": f"2026-01-09T0{h}:00:00+00:00",
            "end": f"2026-01-09T0{h}:30:00+00:00",
            "energy_kwh": 3.5,
        }
        for h in range(0, 10, 2)
    ]
    state = registry.propose("desk", _run(schedule=windows), NOW)
    view = screen(state, NOW, LATEST)
    assert "+2 more windows" in view["lines"]
    assert all(len(line) <= SCREEN_WIDTH for line in view["lines"] + [view["title"]])


def test_proposal_windows_are_timestamps():
    assert Proposal.from_run(_run()).windows() == [(_at("09T02:00"), _at("09T04:00"))]
