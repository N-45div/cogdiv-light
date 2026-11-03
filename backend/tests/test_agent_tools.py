"""The agent's tools: compact, pre-digested answers from the tested planner."""

import pandas as pd
import pytest
from wind_fixtures import NOW, synthetic_carbon, synthetic_wind

from wind_monitor.agent.tools import TOOLS, Toolbox, parse_moment, uk_label


@pytest.fixture
def toolbox() -> Toolbox:
    return Toolbox(synthetic_wind(), NOW)


@pytest.fixture
def green() -> Toolbox:
    """With the synthetic carbon forecast too."""
    return Toolbox(synthetic_wind(), NOW, carbon=synthetic_carbon)


def test_tool_definitions_match_the_toolbox(green):
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {
        "get_wind_outlook",
        "plan_charge",
        "check_recent_wind",
        "get_carbon_outlook",
        "plan_compute_job",
    }
    args = {"plan_charge": {"energy_kwh": 7, "power_kw": 7}, "plan_compute_job": {"hours": 2}}
    for name in names:
        assert "error" not in green.run(name, args.get(name, {}))


def test_uk_labels_and_moments():
    assert uk_label(NOW) == "Thu 18:00"
    assert uk_label(NOW, with_date=True) == "Thu 08 Jan 18:00"
    assert parse_moment("07:00", NOW) == pd.Timestamp("2026-01-09T07:00Z")
    assert parse_moment("2026-01-09T05:30", NOW) == pd.Timestamp("2026-01-09T05:30Z")
    summer = pd.Timestamp("2026-07-01T12:00Z")
    assert parse_moment("18:00", summer) == pd.Timestamp("2026-07-01T17:00Z")  # BST
    with pytest.raises(ValueError, match="HH:MM"):
        parse_moment("tomorrow", NOW)


def test_outlook_is_corrected_and_flags_the_windiest_hour(toolbox):
    out = toolbox.run("get_wind_outlook", '{"hours": 12}')
    assert out["now"] == "Thu 08 Jan 18:00" and out["times"] == "UK local time"
    assert len(out["hours"]) == 12
    assert all(h["expected_mw"] in (10_000, 15_000) for h in out["hours"])
    assert out["windiest_hour"]["time"] == "Fri 02:00"
    assert out["windiest_hour"]["expected_mw"] == 15_000
    assert out["track_record"]["metered_wind_as_pct_of_forecast"]["24h+ ahead"] == 83  # 1/1.2
    assert out["track_record"]["hours_forecast_too_high_pct"] == 100
    assert out["latest_actual"] == {"time": "Thu 17:00", "mw": 10_000}


def test_plan_charge_goes_in_the_windy_hours_and_states_the_gain(toolbox):
    plan = toolbox.run("plan_charge", {"energy_kwh": 14, "power_kw": 7})
    assert plan["feasible"] is True
    assert plan["deadline"] == "Fri 07:00"  # default: next 07:00
    assert plan["charge_windows"] == [{"from": "Fri 02:00", "to": "Fri 04:00", "kwh": 14.0}]
    assert plan["hours_charging"] == 2.0
    assert plan["expected_wind_while_charging_mw"] == 15_000
    assert plan["if_charged_straight_away_mw"] == 10_000
    assert plan["more_wind_than_charging_straight_away_pct"] == 50.0
    assert toolbox.last_plan is not None and toolbox.last_plan.planned_kwh == 14


def test_plan_charge_reads_deadlines_and_reports_shortfalls(toolbox):
    plan = toolbox.run("plan_charge", {"energy_kwh": 30, "power_kw": 7, "deadline": "20:00"})
    assert plan["deadline"] == "Thu 20:00"
    assert plan["feasible"] is False and plan["shortfall_kwh"] == 16.0
    plan = toolbox.run(
        "plan_charge",
        {"energy_kwh": 7, "power_kw": 7, "earliest": "12:00", "deadline": "2026-01-09T01:00"},
    )
    # "12:00" is the next 12:00, i.e. tomorrow, which is after the 01:00 deadline.
    assert plan == {"error": "The deadline must be after the start"}


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("plan_charge", {"energy_kwh": 7, "power_kw": 7, "deadline": "soon"}, "HH:MM"),
        ("plan_charge", {"energy_kwh": 7, "power_kw": 7, "deadline": "2026-01-12T07:00"}, "48"),
        ("plan_charge", {"energy_kwh": 7}, "power_kw"),
        ("plan_charge", {"energy_kwh": 7, "power_kw": 0}, "power_kw"),
        ("launch_rocket", {}, "Unknown tool"),
        ("get_wind_outlook", "{not json", "Expecting"),
    ],
)
def test_bad_calls_come_back_as_errors_the_model_can_fix(toolbox, name, args, message):
    result = toolbox.run(name, args)
    assert message in result["error"]
    assert toolbox.calls[-1] == {"name": name, "arguments": args, "result": result}


def test_recent_wind_verdicts():
    close = Toolbox(synthetic_wind(near=1.02), NOW).run("check_recent_wind", {"hours": 3})
    assert close["verdict"] == "wind is close to forecast"
    assert close["hours_compared"] == 3 and close["actual_as_pct_of_forecast"] == 98
    below = Toolbox(synthetic_wind(near=1.25), NOW).run("check_recent_wind", {"hours": 3})
    assert below["verdict"] == "wind is running below forecast"
    assert below["average_forecast_minus_actual_mw"] == 2_500
    above = Toolbox(synthetic_wind(near=0.8), NOW).run("check_recent_wind", {})
    assert above["verdict"] == "wind is running above forecast"


def test_recent_wind_with_no_actuals_yet():
    early = Toolbox(synthetic_wind(), pd.Timestamp("2026-01-01T00:30Z"))
    assert early.run("check_recent_wind", {})["hours_compared"] == 0


def test_a_wind_plan_also_states_its_carbon(green):
    plan = green.run("plan_charge", {"energy_kwh": 14, "power_kw": 7})
    assert plan["charge_windows"] == [{"from": "Fri 02:00", "to": "Fri 04:00", "kwh": 14.0}]
    assert plan["grid_carbon_while_charging_g_per_kwh"] == 100
    assert plan["grid_carbon_if_charged_straight_away_g_per_kwh"] == 200
    assert plan["less_carbon_than_charging_straight_away_pct"] == 50.0
    assert plan["kg_co2"] == 1.4
    assert green.last_kind == "charge"


def test_a_carbon_plan_takes_the_cleanest_half_hours(green):
    plan = green.run("plan_charge", {"energy_kwh": 14, "power_kw": 7, "goal": "carbon"})
    assert plan["goal"] == "carbon"
    assert [w["from"] for w in plan["charge_windows"]] == ["Fri 02:00", "Fri 04:00"]
    assert plan["grid_carbon_while_charging_g_per_kwh"] == 80
    assert plan["less_carbon_than_charging_straight_away_pct"] == 60.0


def test_without_a_carbon_forecast_wind_plans_still_work(toolbox):
    plan = toolbox.run("plan_charge", {"energy_kwh": 7, "power_kw": 7})
    assert "kg_co2" not in plan and plan["feasible"]
    assert (
        "carbon"
        in toolbox.run("plan_charge", {"energy_kwh": 7, "power_kw": 7, "goal": "carbon"})["error"]
    )
    assert "carbon" in toolbox.run("plan_compute_job", {"hours": 2})["error"]


def test_carbon_outlook(green):
    out = green.run("get_carbon_outlook", {"hours": 12, "region": "London"})
    assert out["gb_now_g_per_kwh"] == 200 and len(out["gb_hours"]) == 12
    assert out["gb_cleanest_hour"] == {"time": "Fri 04:00", "g_per_kwh": 60}
    ranked = out["regions_now_cleanest_first"]
    assert ranked[0] == {"region": "North Scotland", "g_per_kwh": 10, "wind_pct": 90}
    assert out["cleanest_region_now"] == "North Scotland"
    assert out["dirtiest_region_now"] == "South East England"  # 240 g
    assert {"time": "Fri 03:00", "g_per_kwh": 150} in out["region_hours"]


def test_compute_job_goes_where_and_when_the_grid_is_cleanest(green):
    out = green.run("plan_compute_job", {"gpu_hours": 16})
    assert out["feasible"] and out["gpus"] == 8 and out["run_hours"] == 2.0
    assert out["power_kw"] == 10.4 and out["energy_kwh"] == 20.8
    assert out["deadline"] == "Fri 18:00"  # default: 24 hours
    assert out["best"] == {
        "region": "North Scotland",
        "run_windows": [{"from": "Fri 01:00", "to": "Fri 03:00"}],
        "average_g_per_kwh": 0,
        "kg_co2": 0.0,
    }
    assert out["baseline"]["region"] == "London" and out["baseline"]["kg_co2"] == 4.58
    assert out["pct_less_co2_than_baseline"] == 100.0 and out["kg_co2_saved"] == 4.58
    assert out["only_moving_in_time"]["pct_less_co2_than_baseline"] == 15.9
    assert out["only_moving_in_place"]["pct_less_co2_than_baseline"] == 95.5
    assert out["regions_least_co2_first"][0]["region"] == "North Scotland"
    assert green.last_kind == "compute" and green.last_compute.best.region == "North Scotland"


def test_compute_job_errors(green):
    assert "hours" in green.run("plan_compute_job", {"gpus": 8})["error"]
    assert (
        "Unknown region"
        in green.run("plan_compute_job", {"hours": 2, "regions": ["Mars"]})["error"]
    )
    late = green.run("plan_compute_job", {"hours": 30, "deadline": "22:00"})
    assert late["feasible"] is False and "why_not" in late
