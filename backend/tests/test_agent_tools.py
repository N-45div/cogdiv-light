"""The agent's tools: compact, pre-digested answers from the tested planner."""

import pandas as pd
import pytest
from wind_fixtures import NOW, synthetic_wind

from wind_monitor.agent.tools import TOOLS, Toolbox, parse_moment, uk_label


@pytest.fixture
def toolbox() -> Toolbox:
    return Toolbox(synthetic_wind(), NOW)


def test_tool_definitions_match_the_toolbox(toolbox):
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"get_wind_outlook", "plan_charge", "check_recent_wind"}
    for name in names:
        args = {"energy_kwh": 7, "power_kw": 7} if name == "plan_charge" else {}
        assert "error" not in toolbox.run(name, args)


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
