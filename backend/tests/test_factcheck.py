"""The fact checker: every figure in an answer must come from the tools or the question.

The tool result below is a real plan_charge result from the live API on 30 Oct 2026, and the
first two answers are what Muse Spark and Muse Spark Open actually said about it.
"""

import pytest

from wind_monitor.agent.factcheck import check, figures

PLAN = {
    "name": "plan_charge",
    "arguments": {"energy_kwh": 14, "power_kw": 7},
    "result": {
        "feasible": True,
        "energy_kwh": 14.0,
        "power_kw": 7.0,
        "earliest": "Fri 13:17",
        "deadline": "Sat 07:00",
        "charge_windows": [
            {"from": "Fri 13:30", "to": "Fri 14:00", "kwh": 3.5},
            {"from": "Fri 17:00", "to": "Fri 17:30", "kwh": 3.5},
            {"from": "Fri 18:00", "to": "Fri 19:00", "kwh": 7.0},
        ],
        "hours_charging": 2.0,
        "expected_wind_while_charging_mw": 6628,
        "low_case_wind_while_charging_mw": 5363,
        "if_charged_straight_away_mw": 6518,
        "more_wind_than_charging_straight_away_pct": 1.7,
        "forecast_mw": 8283,
    },
}
QUESTION = "My car needs 14 kWh and my charger does 7 kW. When should I charge?"


def _check(answer: str):
    return check(answer, [PLAN], QUESTION)


@pytest.mark.parametrize(
    "answer",
    [
        # Muse Spark 1.3, 30 Oct
        "Charge 13:30–14:00, 17:00–17:30, and 18:00–19:00 UTC on Oct 30 to get your 14 kWh. "
        "The expected wind while charging is about 6628 MW.",
        # Muse Spark Open, 30 Oct
        "Charge on 2026-10-30 at 13:30-14:00 UTC, 17:00-17:30 UTC and 18:00-19:00 UTC to deliver "
        "14 kWh at 7 kW during the windiest periods. This wind-optimized schedule has an expected "
        "wind generation of about 6628 MW (forecast 8283 MW) compared to about 6518 MW if you "
        "charged as soon as possible.",
        "Charge from 6pm to 7pm and finish by 7am: wind should be about 6,600 MW (6.6 GW), "
        "1.7% more than charging now, over 2 hours in 3 windows.",
    ],
)
def test_faithful_answers_pass(answer):
    result = _check(answer)
    assert result.passed, result.unsupported
    assert result.summary.startswith("facts checked")


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("Expected wind while charging is about 7,200 MW.", "7,200 MW"),
        ("Charge from 21:00.", "21:00"),
        ("That is 17% more wind than charging now.", "17%"),
        ("You will charge for 3 hours.", "3 hours"),
        ("Expected wind is 9.1 GW.", "9.1 GW"),
        ("Charge at 8pm.", "8pm"),
        ("About 6,000 MW of wind.", "6,000 MW"),  # 9% off: not an honest rounding
    ],
)
def test_misquoted_or_invented_figures_fail(answer, bad):
    result = _check(answer)
    assert not result.passed
    assert [c.text for c in result.unsupported] == [bad]


def test_summary_counts():
    assert _check("Charge at 18:00 with 6628 MW.").summary == "facts checked 2/2"
    assert _check("Charge at 21:00 with 9999 MW.").summary == "2 facts don't match"
    assert _check("Charge at 21:00 with 6628 MW.").summary == "1 fact doesn't match"
    assert _check("Plug in tonight.").summary == "no figures to check"


def test_sources_are_reported():
    claims = {c.text: c.source for c in _check("Wind 6628 MW from 17:00, 1.7% more.").claims}
    assert claims == {
        "6628 MW": "plan_charge.expected_wind_while_charging_mw",
        "17:00": "plan_charge.charge_windows[1].from",
        "1.7%": "plan_charge.more_wind_than_charging_straight_away_pct",
    }


def test_figures_skip_dates_and_years_and_read_times():
    times, numbers = figures("On 30 Oct 2026 (2026-10-30T13:30Z), Oct 31, charge 9:05 pm-10.30")
    assert times == [("9:05 pm", "21:05"), ("10.30", "10:30")]
    assert numbers == []


def test_numbers_from_the_question_count():
    result = check("Your 40 kWh at 11 kW will finish.", [], "I need 40 kWh at 11 kW")
    assert result.passed and result.summary == "facts checked 2/2"


def test_small_bare_numbers_are_not_checked_but_large_ones_are():
    assert _check("Three windows, step 2 of 3.").summary == "no figures to check"
    assert not _check("Plan reference 4521.").passed


def test_figures_in_result_keys_count():
    outlook = {
        "name": "get_wind_outlook",
        "result": {"track_record": {"metered_wind_as_pct_of_forecast": {"6-12h ahead": 89}}},
    }
    result = check("Forecasts made 6-12h ahead came in at 89%.", [outlook])
    assert result.passed, result.unsupported
