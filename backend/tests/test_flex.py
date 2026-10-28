"""The flexible-load scheduler: windiest slots first, on the chosen basis, within the deadline.

Fixture: an overnight outlook, 18:00 on 14 Jan to 08:00 on 15 Jan, where three hours stand out
and each basis prefers a different one:
    23:00  forecast 16,000 but historically far too high: expected 9,000, low case 8,000
    02:00  forecast 14,000, expected 13,000, but uncertain: low case 6,000
    04:00  forecast 12,000, expected 11,500, low case 10,500 (narrow range)
Every other hour: forecast 8,000, expected 7,000, low case 5,000.
"""

import math

import pandas as pd
import pytest

from wind_monitor.flex import (
    FlexJob,
    blocks,
    plan_load,
    score_against_actuals,
    slots_for,
    wind_during,
)

EVENING = pd.Timestamp("2026-01-14T18:00Z")
MORNING = pd.Timestamp("2026-01-15T08:00Z")
SPECIAL = {
    pd.Timestamp("2026-01-14T23:00Z"): (16_000, 9_000, 8_000),
    pd.Timestamp("2026-01-15T02:00Z"): (14_000, 13_000, 6_000),
    pd.Timestamp("2026-01-15T04:00Z"): (12_000, 11_500, 10_500),
}


def _outlook(until: pd.Timestamp = MORNING) -> pd.DataFrame:
    hours = pd.date_range(EVENING, until, freq="h", inclusive="left")
    rows = [(h, *SPECIAL.get(h, (8_000, 7_000, 5_000))) for h in hours]
    df = pd.DataFrame(rows, columns=["start_time", "forecast_mw", "expected_mw", "low_mw"])
    df["high_mw"] = df["expected_mw"] + 2_000
    df["start_time"] = df["start_time"].dt.as_unit("ns")
    return df


def _job(kwh: float = 7.0, kw: float = 7.0, **kwargs) -> FlexJob:
    earliest = kwargs.pop("earliest", EVENING)
    deadline = kwargs.pop("deadline", MORNING)
    return FlexJob(kwh, kw, earliest, deadline, **kwargs)


def _used(plan) -> dict[str, float]:
    """Energy in each slot in use, keyed by start time (UTC HH:MM)."""
    used = plan.slots[plan.slots["energy_kwh"] > 0]
    return dict(zip(used["start_time"].dt.strftime("%H:%M"), used["energy_kwh"], strict=True))


def _on(plan) -> list[str]:
    return list(_used(plan))


def test_slots_round_inwards_and_take_their_hours_values():
    slots = slots_for(
        _outlook(), pd.Timestamp("2026-01-14T22:10Z"), pd.Timestamp("2026-01-15T00:50Z")
    )
    assert list(slots["start_time"].dt.strftime("%H:%M")) == ["22:30", "23:00", "23:30", "00:00"]
    assert list(slots["forecast_mw"]) == [8_000, 16_000, 16_000, 8_000]


def test_each_basis_picks_its_own_hour():
    assert _on(plan_load(_outlook(), _job(), "raw")) == ["23:00", "23:30"]
    assert _on(plan_load(_outlook(), _job(), "expected")) == ["02:00", "02:30"]
    assert _on(plan_load(_outlook(), _job(), "cautious")) == ["04:00", "04:30"]


def test_energy_adds_up_and_the_remainder_goes_in_the_weakest_slot_used():
    plan = plan_load(_outlook(), _job(kwh=12.0), "expected")  # 3.5 + 3.5 + 3.5 + 1.5
    assert plan.planned_kwh == pytest.approx(12.0)
    assert plan.feasible
    assert _used(plan) == pytest.approx({"02:00": 3.5, "02:30": 3.5, "04:00": 3.5, "04:30": 1.5})


def test_interruptible_job_splits_across_the_best_hours():
    plan = plan_load(_outlook(), _job(kwh=21.0), "expected")  # 6 slots
    assert _on(plan) == ["23:00", "23:30", "02:00", "02:30", "04:00", "04:30"]


def test_uninterruptible_job_takes_the_best_consecutive_run():
    plan = plan_load(_outlook(), _job(kwh=21.0, interruptible=False), "expected")
    # 02:00-05:00 (13,000 / 7,000 / 11,500) beats any other 3-hour run.
    assert _on(plan) == ["02:00", "02:30", "03:00", "03:30", "04:00", "04:30"]
    assert blocks(plan.slots) == [
        {
            "start": pd.Timestamp("2026-01-15T02:00Z"),
            "end": pd.Timestamp("2026-01-15T05:00Z"),
            "energy_kwh": 21.0,
        }
    ]


def test_asap_baseline_starts_straight_away():
    plan = plan_load(_outlook(), _job(), "expected")
    asap = plan.asap[plan.asap["energy_kwh"] > 0]
    assert list(asap["start_time"].dt.strftime("%H:%M")) == ["18:00", "18:30"]
    assert wind_during(plan.slots, "expected_mw") == 13_000
    assert wind_during(plan.asap, "expected_mw") == 7_000


def test_too_much_energy_for_the_window_uses_every_slot_and_reports_the_gap():
    job = _job(kwh=16.0, earliest=pd.Timestamp("2026-01-15T06:00Z"))  # 4 slots = 14 kWh
    plan = plan_load(_outlook(), job, "expected")
    assert plan.planned_kwh == pytest.approx(14.0)
    assert plan.shortfall_kwh == pytest.approx(2.0)
    assert not plan.feasible


def test_slots_past_the_forecast_horizon_are_used_only_if_needed():
    outlook = _outlook(until=pd.Timestamp("2026-01-14T20:00Z"))  # forecast only to 20:00
    plan = plan_load(outlook, _job(kwh=3.5, deadline=pd.Timestamp("2026-01-14T22:00Z")))
    assert _on(plan) == ["18:00"]  # a known slot beats an unknown one
    plan = plan_load(outlook, _job(kwh=21.0, deadline=pd.Timestamp("2026-01-14T22:00Z")))
    assert plan.feasible and len(_on(plan)) == 6
    assert wind_during(plan.slots, "forecast_mw") is None  # part of the run is unknown


def test_without_error_history_the_raw_forecast_is_used():
    outlook = _outlook().assign(expected_mw=math.nan, low_mw=math.nan, high_mw=math.nan)
    assert _on(plan_load(outlook, _job(), "expected")) == ["23:00", "23:30"]


def test_nothing_to_do():
    plan = plan_load(_outlook(), _job(kwh=0.0))
    assert plan.planned_kwh == 0 and plan.feasible and blocks(plan.slots) == []
    late = _job(earliest=MORNING, deadline=EVENING)  # deadline before the start
    plan = plan_load(_outlook(), late)
    assert plan.slots.empty and not plan.feasible


@pytest.mark.parametrize(
    ("kwargs", "basis"),
    [({"kw": 0.0}, "expected"), ({"kwh": -1.0}, "expected"), ({}, "optimistic")],
)
def test_bad_input_is_rejected(kwargs, basis):
    with pytest.raises(ValueError):
        plan_load(_outlook(), _job(**kwargs), basis)


def test_score_against_actuals_uses_each_half_hour():
    plan = plan_load(_outlook(), _job(kwh=5.25), "expected")  # 3.5 at 02:00, 1.75 at 02:30
    actuals = pd.DataFrame(
        {
            "start_time": pd.to_datetime(["2026-01-15T02:00Z", "2026-01-15T02:30Z"]).as_unit("ns"),
            "generation_mw": [12_000.0, 9_000.0],
        }
    )
    assert score_against_actuals(plan.slots, actuals) == pytest.approx(11_000.0)
    assert score_against_actuals(plan.slots, actuals.iloc[:1]) is None
