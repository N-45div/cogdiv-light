"""Siting and timing a compute job for the least CO2 (synthetic carbon: see wind_fixtures)."""

import pandas as pd
import pytest
from wind_fixtures import NOW, synthetic_carbon

from wind_monitor.compute import ComputeJob, kg_co2, plan_compute
from wind_monitor.flex import blocks

MORNING = pd.Timestamp("2026-01-09T09:00Z")


def _job(gpus=8, hours=2.0, deadline=MORNING, pause=True) -> ComputeJob:
    return ComputeJob(gpus, hours, NOW, deadline, pause)


def _hours(slots) -> list[tuple[str, str]]:
    return [(f"{b['start']:%H:%M}", f"{b['end']:%H:%M}") for b in blocks(slots)]


def test_power_and_energy_come_from_the_gpu_count():
    job = _job()
    assert job.power_kw == 10.4 and job.energy_kwh == 20.8


def test_the_job_goes_to_the_cleanest_region_at_its_cleanest_time():
    plan = plan_compute(synthetic_carbon(), _job())
    best = plan.best
    assert best.region == "North Scotland"
    assert _hours(best.slots) == [("01:00", "03:00")]  # 0 g/kWh; earliest of the ties
    assert best.kg_co2 == 0
    # Baseline: London straight away at 220 g/kWh.
    assert plan.baseline.region == "London"
    assert _hours(plan.baseline.asap) == [("18:00", "20:00")]
    assert plan.baseline.asap_kg_co2 == pytest.approx(20.8 * 0.220)
    # London at its cleanest time: the 150 g hour, plus the earliest 220 g hour.
    assert _hours(plan.baseline.slots) == [("18:00", "19:00"), ("03:00", "04:00")]
    assert plan.baseline.kg_co2 == pytest.approx(10.4 * 0.150 + 10.4 * 0.220)


def test_regions_are_ranked_and_can_be_limited():
    plan = plan_compute(synthetic_carbon(), _job(), regions=[13, 7, 5])
    assert [s.region for s in plan.sites] == ["Yorkshire", "South Wales", "London"]
    assert plan.best.region == "Yorkshire"


def test_a_job_that_cannot_pause_takes_one_run():
    plan = plan_compute(synthetic_carbon(), _job(hours=3, pause=False), regions=[13])
    # Every 3-hour run holding the 150 g hour ties; the earliest wins.
    assert _hours(plan.best.slots) == [("01:00", "04:00")]


def test_a_job_too_long_for_its_window_is_not_feasible():
    plan = plan_compute(synthetic_carbon(), _job(hours=20, deadline=NOW + pd.Timedelta(hours=4)))
    assert not plan.feasible and plan.best is None


def test_no_forecast_means_no_figure():
    slots = pd.DataFrame({"start_time": [NOW], "carbon_g": [float("nan")], "energy_kwh": [5.0]})
    assert kg_co2(slots) is None


@pytest.mark.parametrize("bad", [{"gpus": 0}, {"hours": 0}])
def test_bad_jobs(bad):
    args = {"gpus": 8, "hours": 2.0} | bad
    with pytest.raises(ValueError):
        ComputeJob(args["gpus"], args["hours"], NOW, MORNING)
