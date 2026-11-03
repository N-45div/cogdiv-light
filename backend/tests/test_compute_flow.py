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
from wind_fixtures import NOW, synthetic_carbon, synthetic_wind

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


def test_the_watcher_leaves_compute_jobs_alone():
    registry = DeviceRegistry()
    run = _compute_run()
    state = registry.propose("desk-1", run, NOW)
    registry.event("desk-1", {"type": "approve", "proposal_id": state.proposal.id}, NOW)
    assert Watcher().run(NOW, registry, synthetic_wind(), None) == []


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
