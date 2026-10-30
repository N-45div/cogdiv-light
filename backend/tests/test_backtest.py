"""The backtest strategies, on a made-up month where the answer is known.

Actual wind is 10,000 MW, except 03:00-04:00 every night at 15,000 MW. A run every 2 hours
forecasts the actual exactly, except that runs published before 20:00 the evening before get
the night wrong: they put the windy hour at 22:00 instead of 03:00. So a plan made at an 18:00
plug-in picks 22:00 and misses; re-planning on the 20:00 run catches 03:00.
"""

from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from wind_monitor import backtest as bt
from wind_monitor.store import WindData

FIRST = pd.Timestamp("2026-01-01T00:00Z")


def _actual(t: pd.Timestamp) -> float:
    return 15_000.0 if t.hour == 3 else 10_000.0


def _forecast(published: pd.Timestamp, target: pd.Timestamp) -> float:
    evening = (target - pd.Timedelta(hours=12)).normalize() + pd.Timedelta(hours=20)
    if published < evening:  # made before 20:00 the evening before: wrong about the night
        if target.hour == 22:
            return 15_000.0
        if target.hour == 3:
            return 10_000.0
    return _actual(target)


@pytest.fixture(scope="module")
def data() -> WindData:
    halves = pd.date_range(FIRST, periods=48 * 8, freq="30min")
    actuals = pd.DataFrame({"start_time": halves, "generation_mw": [_actual(t) for t in halves]})
    rows = [
        (p, p + pd.Timedelta(hours=h), _forecast(p, p + pd.Timedelta(hours=h)))
        for p in pd.date_range(FIRST - pd.Timedelta(days=2), periods=12 * 10, freq="2h")
        for h in range(1, 49)
    ]
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


SETUP = bt.Night(energy_kwh=7.0, power_kw=7.0)  # one hour of charging
NIGHT = date(2026, 1, 4)


def _times(slots: pd.DataFrame) -> list[str]:
    on = slots[slots["energy_kwh"] > 0].sort_values("start_time")
    return list(on["start_time"].dt.strftime("%d %H:%M"))


def test_each_strategy_on_one_night(data):
    row = bt.night(data, NIGHT, SETUP)
    assert row["plug_in"] == 10_000  # 18:00-19:00
    assert row["timer"] == 10_000  # 00:00-01:00
    assert row["once"] == 10_000 and row["once_raw"] == 10_000  # fooled into 22:00
    assert row["replan"] == 15_000  # the 20:00 run moved it to 03:00
    assert row["hindsight"] == 15_000


def test_replan_moves_the_charge_when_a_new_run_changes_its_mind(data):
    job = SETUP.job(NIGHT)
    assert _times(bt.replan(data, job)) == ["05 03:00", "05 03:30"]


def test_replan_keeps_what_already_ran(data):
    # 28 kWh = 4 hours from 18:00: some charging happens before the 20:00 run and stays.
    job = bt.Night(energy_kwh=28.0).job(NIGHT)
    used = bt.replan(data, job)
    assert used["energy_kwh"].sum() == pytest.approx(28.0)
    assert used["start_time"].is_unique
    assert used["start_time"].min() >= job.earliest and used["start_time"].max() < job.deadline
    assert "05 03:00" in _times(used)


def test_timer_and_plug_in_slots():
    job = SETUP.job(NIGHT)
    assert _times(bt._first_slots(job, job.earliest)) == ["04 18:00", "04 18:30"]
    partial = bt.Night(energy_kwh=5.0).job(NIGHT)
    assert list(bt._first_slots(partial, partial.earliest)["energy_kwh"]) == [3.5, 1.5]


def test_hindsight_takes_the_windiest_half_hours(data):
    used = bt.hindsight(data, bt.Night(energy_kwh=5.0).job(NIGHT))
    assert _times(used) == ["05 03:00", "05 03:30"]
    assert sorted(used["energy_kwh"]) == [1.5, 3.5]


def test_timer_before_plug_in_means_the_next_morning(data):
    setup = bt.Night(energy_kwh=7.0, timer_start=time(1, 0))
    assert bt.night(data, NIGHT, setup)["timer"] == 10_000


def test_summary_reports_share_of_the_possible_gain():
    nights = pd.DataFrame(
        {
            "plug_in": [10_000.0, 10_000.0],
            "once": [12_500.0, 10_000.0],
            "hindsight": [15_000.0, 15_000.0],
        }
    )
    table = bt.summary(nights)
    assert table.loc["once", "share of possible gain"] == "25%"
    assert table.loc["once", "nights better than plug-in"] == "1/2"
    assert table.loc["hindsight", "vs plug-in"] == "+50.0%"
