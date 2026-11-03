"""The grid brief: facts by code, words by the open-weight model, checked like any answer."""

from dataclasses import replace

from fastapi.testclient import TestClient
from test_agent_api import Planner
from test_agent_runner import say
from wind_fixtures import NOW, synthetic_carbon

from wind_monitor.agent import factcheck
from wind_monitor.agent.llm import ModelUnavailableError
from wind_monitor.brief import BriefDesk, brief_facts, get_brief_desk, make_brief, plain_brief
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.main import create_app

GOOD = "North Scotland is cleanest now at 10 g/kWh. Across GB, 03:00-05:00 is best, at 80 g/kWh."


def test_the_facts():
    facts = brief_facts(NOW, synthetic_carbon())
    assert facts["now"] == "Thu 08 Jan 18:00" and facts["gb_now_g_per_kwh"] == 200
    assert facts["cleanest_region_now"]["region"] == "North Scotland"
    assert facts["dirtiest_region_now"]["region"] == "South East England"
    # GB: 100 g at 02:00-04:00 and 60 g at 04:00-05:00, so 03:00-05:00 averages 80.
    assert facts["gb_cleanest_two_hours"] == {
        "from": "Fri 03:00",
        "to": "Fri 05:00",
        "average_g_per_kwh": 80,
    }
    job = facts["example_compute_job"]
    assert job["best_region"] == "North Scotland" and job["hours"] == 4.0
    assert job["run_windows"] == [{"from": "Fri 01:00", "to": "Fri 05:00"}]
    assert job["pct_less_co2_than_starting_now_in_london"] == 100.0


def test_the_code_version_passes_its_own_fact_check():
    facts = brief_facts(NOW, synthetic_carbon())
    text = plain_brief(facts)
    assert text.startswith("Right now the grid is cleanest in North Scotland (10 g/kWh)")
    assert "best run in North Scotland from 01:00: 100% less CO2" in text
    assert factcheck.check(text, [{"name": "grid_facts", "result": facts}]).passed


def test_the_model_writes_it_when_its_figures_check_out():
    brief = make_brief(NOW, synthetic_carbon(), Planner(say(GOOD)))
    assert brief.text == GOOD and brief.by == "fake:model"
    assert brief.check.startswith("facts checked")


def test_a_wrong_figure_gets_one_rewrite_then_the_code_version():
    wrong = "North Scotland is at 10 g/kWh; 02:00-04:00 is best at 55 g/kWh."
    brief = make_brief(NOW, synthetic_carbon(), Planner(say(wrong), say(wrong)))
    assert brief.by == "code" and brief.check == "the model's text failed the fact check"
    fixed = make_brief(NOW, synthetic_carbon(), Planner(say(wrong), say(GOOD)))
    assert fixed.text == GOOD


def test_no_model_means_the_code_version():
    assert make_brief(NOW, synthetic_carbon(), None).by == "code"
    down = make_brief(NOW, synthetic_carbon(), Planner(ModelUnavailableError("down", [])))
    assert down.by == "code" and down.check.startswith("model unavailable")


def test_the_desk_writes_once_per_half_hour(synthetic_carbon_feed):
    desk = BriefDesk()
    first = desk.refresh(NOW, synthetic_carbon_feed, None)
    assert desk.refresh(NOW.replace(minute=20), synthetic_carbon_feed, None) is first
    assert not desk.due(NOW.replace(minute=29)) and desk.due(NOW.replace(minute=30))


def test_brief_endpoint():
    app = create_app(replace(Settings.from_env(), static_dir=None, watch=False))
    app.dependency_overrides[get_clock] = lambda: ReplayClock(NOW)
    app.dependency_overrides[get_brief_desk] = BriefDesk
    body = TestClient(app).get("/api/brief").json()
    assert body["by"] == "code" and body["at"] == "2026-01-08T18:00:00+00:00"
    assert body["text"].startswith("Right now the grid is cleanest in North Scotland")
