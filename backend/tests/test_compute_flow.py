"""A compute job end to end: asked, planned for the least CO2, fact-checked, sent to the board,
approved with the button, and the relay following its windows. Plus the carbon and compute
endpoints. Synthetic carbon (wind_fixtures): North Scotland is 0 g/kWh from 01:00 to 06:00,
London 220 g/kWh."""

from dataclasses import replace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from test_agent_api import Planner
from test_agent_runner import FakeModel, call, say
from wind_fixtures import NOW, carbon_g, synthetic_carbon, synthetic_wind

from wind_monitor import agent_api, planning_api
from wind_monitor.agent.runner import run_agent
from wind_monitor.agent.runs import RunLog, get_run_log
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.device import DeviceRegistry, get_registry, news, screen
from wind_monitor.main import create_app
from wind_monitor.watcher import Watcher

QUESTION = "I need to run 16 GPU-hours of fine-tuning before 9 am. Where and when?"
COMPUTE_CALL = call("plan_compute_job", {"gpu_hours": 16, "deadline": "09:00"})
ANSWER = "Run it in North Scotland 01:00-03:00: 100% less CO2 than starting now in London."
LATEST = {"time": "2026-01-08T17:00:00+00:00", "actual_mw": 10_000.0}


def _compute_run():
    model = FakeModel(COMPUTE_CALL, say(ANSWER))
    return run_agent(QUESTION, synthetic_wind(), NOW, model, carbon=synthetic_carbon)


def test_the_agent_plans_a_compute_job_and_its_answer_is_checked():
    run = _compute_run()
    assert run.verified and run.kind == "compute"
    assert run.plan["best"]["region"] == "North Scotland"
    assert run.schedule == [
        {
            "start": "2026-01-09T01:00:00+00:00",
            "end": "2026-01-09T03:00:00+00:00",
            "energy_kwh": 20.8,
        }
    ]
    assert run.job["kind"] == "compute" and run.job["region_id"] == 1 and run.job["gpus"] == 8


def test_the_board_shows_says_and_runs_the_compute_plan():
    registry = DeviceRegistry()
    state = registry.propose("desk-1", _compute_run(), NOW)
    view = screen(state, NOW, LATEST)
    assert view["title"] == "Compute plan?" and view["prompt"] == "hold:yes  tap:no"
    assert view["lines"] == [
        "01:00-03:00",
        "in North Scotland",
        "~0 g/kWh",
        "-100% CO2 vs London",
        "facts checked 3/3",
    ]
    speech = news(state, NOW, 10_000)
    assert speech.startswith("Here's my plan: run the job in North Scotland, from 01:00 to 03:00")
    assert "100 percent less carbon than starting now in London" in speech
    assert len(speech) < 220  # short enough to say in about ten seconds

    registry.event("desk-1", {"type": "approve", "proposal_id": state.proposal.id}, NOW)
    assert news(state, NOW, 10_000) == "Great, it's booked. I'll start the job at 01:00."
    running = pd.Timestamp("2026-01-09T01:15Z")
    assert news(state, running, 10_000).startswith("The compute job is running now")
    assert screen(state, running, LATEST)["title"] == "JOB RUNNING"
    done = pd.Timestamp("2026-01-09T03:00Z")
    assert news(state, done, 10_000) == (
        "All done! The job finished with 0 kilograms of carbon, instead of 4.58 in London."
    )
    assert screen(state, done, LATEST)["title"] == "Job done"


def _approved_job() -> DeviceRegistry:
    registry = DeviceRegistry()
    state = registry.propose("desk-1", _compute_run(), NOW)  # North Scotland, 01:00-03:00
    registry.event("desk-1", {"type": "approve", "proposal_id": state.proposal.id}, NOW)
    registry.next_news("desk-1", NOW, None)  # the proposal was announced
    registry.next_news("desk-1", NOW, None)  # and "Great, it's booked"
    return registry


CHECK = NOW + pd.Timedelta(hours=1)


def test_without_a_carbon_forecast_compute_jobs_are_not_checked():
    assert Watcher().run(CHECK, _approved_job(), synthetic_wind(), None) == []


def test_a_job_on_track_stays_quiet():
    registry = _approved_job()
    [finding] = Watcher().run(CHECK, registry, synthetic_wind(), None, synthetic_carbon(CHECK))
    assert finding.outcome == "on_track"
    assert finding.message == "On track: 20.8 kWh still to run in North Scotland, Fri 01:00-03:00."


def test_a_cleaner_window_elsewhere_is_proposed():
    def changed(region, t):  # North Scotland's early hours turn dirty; 04:00-06:00 stay clean
        if region == 1 and t.hour < 4:
            return 300.0
        return carbon_g(region, t)

    registry = _approved_job()
    [finding] = Watcher().run(
        CHECK, registry, synthetic_wind(), None, synthetic_carbon(CHECK, g=changed)
    )
    assert finding.outcome == "better_window"
    assert finding.facts["newest_g_per_kwh_for_those_windows"] == 300
    assert finding.facts["better_region"] == "North Scotland"
    assert finding.facts["better_windows"] == ["Fri 04:00-06:00"]
    assert finding.facts["less_co2_than_current_plan_pct"] == 100.0

    state = registry.all()[0]
    view = screen(state, CHECK, LATEST)
    assert view["title"] == "Better window?" and "-100% CO2 vs old plan" in view["lines"]
    speech = news(state, CHECK, None)
    assert speech.startswith("Good news, the carbon forecast has changed. Running the job in")
    registry.event("desk-1", {"type": "approve", "proposal_id": finding.proposal_id}, CHECK)
    assert state.active.kind == "compute"
    assert state.active.schedule[0]["start"].startswith("2026-01-09T04:00")


def test_dirtier_windows_with_nowhere_cleaner_show_an_alert():
    registry = _approved_job()
    flat = synthetic_carbon(CHECK, g=lambda region, t: 250.0)
    [finding] = Watcher().run(CHECK, registry, synthetic_wind(), None, flat)
    assert finding.outcome == "carbon_up"
    assert finding.facts["more_g_per_kwh_than_planned"] == 250
    state = registry.all()[0]
    assert state.alert == "Carbon +250 g vs plan"
    assert "Carbon +250 g vs plan" in screen(state, CHECK, LATEST)["lines"]


@pytest.fixture
def client():
    data, log, registry = synthetic_wind(), RunLog(), DeviceRegistry()
    app = create_app(replace(Settings.from_env(), static_dir=None, watch=False))
    app.dependency_overrides[planning_api.planning_data] = lambda: data
    app.dependency_overrides[get_clock] = lambda: ReplayClock(NOW)
    app.dependency_overrides[get_run_log] = lambda: log
    app.dependency_overrides[get_registry] = lambda: registry
    return app


def test_carbon_endpoint(client):
    body = TestClient(client).get("/api/carbon?hours=12").json()
    assert len(body["times"]) == 24 and body["times"][0].startswith("2026-01-08T18:00")
    assert body["gb_now_g"] == 200 and len(body["national"]) == 24
    first = body["regions"][0]
    assert first["name"] == "North Scotland" and first["now_g"] == 10
    assert first["mix"] == {"wind": 90.0, "gas": 5.0}
    assert body["regions"][-1]["name"] == "South East England"
    assert len(first["g"]) == 24 and first["g"][14] == 0  # 01:00


def test_compute_plan_endpoint(client):
    body = (
        TestClient(client)
        .post("/api/compute/plan", json={"gpus": 8, "hours": 2, "deadline": "2026-01-09T09:00Z"})
        .json()
    )
    assert body["feasible"] and body["job"]["energy_kwh"] == 20.8
    assert body["best"]["region"] == "North Scotland" and body["best"]["kg_co2"] == 0
    assert body["best"]["windows"][0]["start"].startswith("2026-01-09T01:00")
    assert body["baseline"]["region"] == "London" and body["baseline"]["kg_co2"] == 4.576
    assert body["time_only"]["kg_co2"] == 3.848
    assert body["place_only"]["region"] == "North Scotland"
    assert body["pct_less_co2"] == 100.0 and body["kg_co2_saved"] == 4.576
    assert len(body["sites"]) == 14


@pytest.mark.parametrize(
    ("job", "message"),
    [
        ({"hours": 2, "deadline": "2026-01-08T17:00Z"}, "after the start"),
        ({"hours": 2, "deadline": "2026-01-12T09:00Z"}, "48 hours"),
        ({"hours": 2, "regions": [99]}, "Unknown region"),
    ],
)
def test_compute_plan_rejects_bad_jobs(client, job, message):
    response = TestClient(client).post("/api/compute/plan", json=job)
    assert response.status_code == 422 and message in response.json()["detail"]


def test_ask_about_compute_sends_the_plan_to_the_board(client):
    planner = Planner(COMPUTE_CALL, say(ANSWER))
    client.dependency_overrides[agent_api.get_planner] = lambda: planner
    body = (
        TestClient(client)
        .post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"})
        .json()
    )
    assert body["kind"] == "compute" and body["verified"] and body["proposed_to"] == "desk-1"
    state = TestClient(client).get("/api/device/desk-1/state").json()
    assert state["title"] == "Compute plan?" and "in North Scotland" in state["lines"]
