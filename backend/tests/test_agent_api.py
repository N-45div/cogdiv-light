"""The agent API: /api/agent/ask runs the agent; /api/agent/runs is the log of what it did."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_agent_runner import GOOD, PLAN_CALL, FakeModel, say
from wind_fixtures import NOW, synthetic_wind

from wind_monitor import agent_api, planning_api
from wind_monitor.agent.llm import ModelUnavailableError
from wind_monitor.agent.runs import RunLog, get_run_log
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.main import create_app

QUESTION = "I need 14 kWh at 7 kW, when should I charge?"


class Planner(FakeModel):
    def __init__(self, *replies, configured: bool = True) -> None:
        super().__init__(*replies)
        self.configured = configured

    def available(self) -> list:
        return ["fake:model"] if self.configured else []


@pytest.fixture
def make_client():
    def make(planner: Planner) -> TestClient:
        data, log = synthetic_wind(), RunLog()
        app = create_app(replace(Settings.from_env(), static_dir=None))
        app.dependency_overrides[planning_api.planning_data] = lambda: data
        app.dependency_overrides[get_clock] = lambda: ReplayClock(NOW)
        app.dependency_overrides[agent_api.get_planner] = lambda: planner
        app.dependency_overrides[get_run_log] = lambda: log
        return TestClient(app)

    return make


def test_ask_returns_the_checked_answer_and_full_record(make_client):
    client = make_client(Planner(PLAN_CALL, say(GOOD)))
    body = client.post("/api/agent/ask", json={"question": QUESTION}).json()
    assert body["answer"] == GOOD and body["verified"] is True
    assert body["factcheck"]["summary"] == "facts checked 4/4"
    assert body["as_of"] == "2026-01-08T18:00:00+00:00" and body["mode"] == "replay"
    assert body["models"] == ["fake:model", "fake:model"]
    assert body["tool_calls"][0]["name"] == "plan_charge"
    assert body["plan"]["more_wind_than_charging_straight_away_pct"] == 50.0


def test_runs_are_logged_newest_first(make_client):
    client = make_client(Planner(PLAN_CALL, say(GOOD), say("Plug in later.")))
    first = client.post("/api/agent/ask", json={"question": QUESTION}).json()
    second = client.post("/api/agent/ask", json={"question": "Anything?"}).json()
    runs = client.get("/api/agent/runs").json()
    assert [r["id"] for r in runs] == [second["id"], first["id"]]
    assert runs[1]["summary"] == "facts checked 4/4" and runs[1]["verified"] is True
    assert client.get(f"/api/agent/runs/{first['id']}").json()["answer"] == GOOD
    assert client.get("/api/agent/runs/nope").status_code == 404


def test_no_model_configured_is_a_503(make_client):
    client = make_client(Planner(configured=False))
    response = client.post("/api/agent/ask", json={"question": QUESTION})
    assert response.status_code == 503 and "MODEL_API_KEY" in response.json()["detail"]


def test_every_model_failing_is_a_503_but_still_logged(make_client):
    client = make_client(Planner(ModelUnavailableError("Every configured model failed", [])))
    response = client.post("/api/agent/ask", json={"question": QUESTION})
    assert response.status_code == 503
    assert client.get("/api/agent/runs").json()[0]["answer"] is None


@pytest.mark.parametrize("question", ["", "x" * 501])
def test_question_length_is_checked(make_client, question):
    client = make_client(Planner())
    assert client.post("/api/agent/ask", json={"question": question}).status_code == 422
