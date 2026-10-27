"""Tests for the core business rules, on tiny hand-made tables where the answer is obvious."""

from datetime import date, time

import pandas as pd
import pytest

from wind_monitor import analysis as an

DAY = date(2026, 1, 15)
CUTOFF = time(9, 20)


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC").as_unit("ns")


def forecasts(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """rows: (publish_time, start_time, generation_mw)"""
    df = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    df["publish_time"] = pd.to_datetime(df["publish_time"], utc=True).dt.as_unit("ns")
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True).dt.as_unit("ns")
    return df


def actuals(rows: list[tuple[str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["start_time", "generation_mw"])
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True).dt.as_unit("ns")
    return df


# ---- time rules ------------------------------------------------------------------------


def test_cutoff_is_uk_time_on_the_day_before_delivery():
    assert an.decision_cutoff(DAY, CUTOFF) == ts("2026-01-14T09:20")  # GMT = UTC in winter


def test_cutoff_respects_british_summer_time():
    assert an.decision_cutoff(date(2026, 7, 15), CUTOFF) == ts("2026-07-14T08:20")


def test_delivery_window_is_uk_midnight_to_midnight():
    assert an.delivery_window(DAY) == (ts("2026-01-15T00:00"), ts("2026-01-16T00:00"))


def test_clock_change_day_is_23_hours_long():
    start, end = an.delivery_window(date(2026, 3, 29))
    assert end - start == pd.Timedelta(hours=23)


# ---- which forecast was available at decision time ------------------------------------


def test_picks_latest_run_published_before_cutoff():
    fc = forecasts(
        [
            ("2026-01-14T03:30", "2026-01-15T10:00", 1000),  # older run
            ("2026-01-14T08:30", "2026-01-15T10:00", 2000),  # latest before cutoff -> used
            ("2026-01-14T11:30", "2026-01-15T10:00", 3000),  # after cutoff -> not available
        ]
    )
    result = an.decision_forecast(fc, DAY, CUTOFF)
    assert result["forecast_mw"].tolist() == [2000]
    assert result["publish_time"].tolist() == [ts("2026-01-14T08:30")]


def test_run_published_exactly_at_cutoff_counts_as_available():
    fc = forecasts([("2026-01-14T09:20", "2026-01-15T10:00", 2000)])
    assert len(an.decision_forecast(fc, DAY, CUTOFF)) == 1


def test_older_run_fills_hours_the_latest_run_does_not_cover():
    fc = forecasts(
        [
            ("2026-01-14T03:30", "2026-01-15T10:00", 1000),
            ("2026-01-14T03:30", "2026-01-15T23:00", 1100),
            ("2026-01-14T08:30", "2026-01-15T10:00", 2000),  # latest run stops early
        ]
    )
    result = an.decision_forecast(fc, DAY, CUTOFF)
    assert result["forecast_mw"].tolist() == [2000, 1100]


def test_ignores_targets_outside_the_delivery_day():
    fc = forecasts(
        [
            ("2026-01-14T08:30", "2026-01-14T23:00", 500),
            ("2026-01-14T08:30", "2026-01-16T00:00", 500),
        ]
    )
    assert an.decision_forecast(fc, DAY, CUTOFF).empty


# ---- errors ----------------------------------------------------------------------------


def test_errors_compare_on_the_hour_with_forecast_minus_actual():
    fc = forecasts([("2026-01-14T08:30", "2026-01-15T10:00", 2000)])
    act = actuals([("2026-01-15T10:00", 1500), ("2026-01-15T10:30", 1600)])

    errors = an.decision_errors(fc, act, [DAY], CUTOFF)

    assert len(errors) == 1  # the :30 actual has no forecast to compare with
    row = errors.iloc[0]
    assert row["error_mw"] == 500  # over-forecast -> desk is short
    assert row["lead_hours"] == pytest.approx(25.5)
    assert row["hour"] == 10
    assert row["delivery_date"] == DAY


def test_error_summary_numbers():
    errors = pd.Series([100.0, -300.0, 200.0, 0.0])
    summary = an.error_summary(errors, pd.Series([1000.0] * 4))
    assert summary["n"] == 4
    assert summary["mae_mw"] == 150
    assert summary["bias_mw"] == 0
    assert summary["rmse_mw"] == pytest.approx((140000 / 4) ** 0.5)
    assert summary["max_over_forecast_mw"] == 200
    assert summary["max_under_forecast_mw"] == 300
    assert summary["nmae_pct"] == 15
    assert summary["over_forecast_share_pct"] == 50
    assert summary["debiased_mae_mw"] == 150  # bias is 0, so same as MAE


def test_debiased_error_removes_a_constant_offset():
    summary = an.error_summary(pd.Series([900.0, 1100.0]), pd.Series([5000.0, 5000.0]))
    assert summary["bias_mw"] == 1000
    assert summary["debiased_mae_mw"] == 100


def test_error_summary_of_nothing():
    assert an.error_summary(pd.Series(dtype=float), pd.Series(dtype=float)) == {"n": 0}


def test_day_energy_at_both_resolutions():
    half_hourly = pd.Series(pd.date_range("2026-01-15", periods=48, freq="30min", tz="UTC"))
    hourly = pd.Series(pd.date_range("2026-01-15", periods=24, freq="h", tz="UTC"))
    assert an.day_energy_mwh(half_hourly, pd.Series([1000.0] * 48), DAY, an.HALF_HOUR) == 24000
    assert an.day_energy_mwh(hourly, pd.Series([1000.0] * 24), DAY, an.HOUR) == 24000


def test_day_energy_is_withheld_when_any_period_is_missing():
    times = pd.Series(pd.date_range("2026-01-15", periods=48, freq="30min", tz="UTC"))
    values = pd.Series([1000.0] * 48)
    assert an.day_energy_mwh(times[:-1], values[:-1], DAY, an.HALF_HOUR) is None
    values[5] = float("nan")
    assert an.day_energy_mwh(times, values, DAY, an.HALF_HOUR) is None


def test_day_energy_on_a_23_hour_clock_change_day():
    spring = date(2026, 3, 29)
    assert an.expected_periods(spring, an.HALF_HOUR) == 46
    times = pd.Series(pd.date_range("2026-03-29", periods=46, freq="30min", tz="UTC"))
    assert an.day_energy_mwh(times, pd.Series([1000.0] * 46), spring, an.HALF_HOUR) == 23000


def test_decision_errors_with_no_days_is_an_empty_table():
    fc = forecasts([("2026-01-14T08:30", "2026-01-15T10:00", 2000)])
    act = actuals([("2026-01-15T10:00", 1500)])
    errors = an.decision_errors(fc, act, [], CUTOFF)
    assert errors.empty
    assert {"delivery_date", "error_mw", "lead_hours", "hour"} <= set(errors.columns)


def test_histogram_counts_every_error():
    errors = pd.Series([-900.0, -100.0, 50.0, 400.0, 1200.0])
    bins = an.error_histogram(errors, bin_mw=500)
    assert sum(b["count"] for b in bins) == 5
    assert bins[0]["lower_mw"] == -1000
    assert bins[-1]["upper_mw"] == 1500


def test_histogram_when_every_error_is_the_same():
    assert an.error_histogram(pd.Series([100.0, 100.0])) == [
        {"lower_mw": 0.0, "upper_mw": 500.0, "count": 2}
    ]
    assert an.error_histogram(pd.Series(dtype=float)) == []


def test_error_by_hour_groups_on_uk_hour():
    df = pd.DataFrame({"hour": [1, 1, 2], "error_mw": [100.0, -100.0, 50.0]})
    rows = an.error_by_hour(df)
    assert [r["hour"] for r in rows] == [1, 2]
    assert rows[0]["mae_mw"] == 100
    assert rows[0]["mean_error_mw"] == 0


# ---- uncertainty band -------------------------------------------------------------------


def test_band_low_case_uses_high_error_quantile_and_is_clipped_at_zero():
    history = pd.DataFrame({"hour": [10] * 11, "error_mw": [float(e) for e in range(0, 1100, 100)]})
    day = pd.DataFrame(
        {"start_time": [ts("2026-01-15T10:00")], "forecast_mw": [500.0], "publish_time": [None]}
    )
    banded = an.add_uncertainty_band(day, history)
    # errors 0..1000: q10 = 100, q50 = 500, q90 = 900.
    assert banded["expected_mw"].iloc[0] == pytest.approx(0)  # 500 - 500
    assert banded["low_mw"].iloc[0] == 0  # 500 - 900, clipped at zero
    assert banded["high_mw"].iloc[0] == pytest.approx(400)  # 500 - 100


def _day_at_ten(forecast_mw: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_time": [ts("2026-01-15T10:00")],
            "forecast_mw": [forecast_mw],
            "publish_time": [None],
        }
    )


def test_band_falls_back_to_all_hours_when_hour_has_no_history():
    history = pd.DataFrame({"hour": [3] * 10, "error_mw": [-100.0, 100.0] * 5})
    banded = an.add_uncertainty_band(_day_at_ten(5000.0), history)
    assert banded["low_mw"].iloc[0] == pytest.approx(5000 - 100)
    assert banded["high_mw"].iloc[0] == pytest.approx(5000 + 100)


def test_band_ignores_an_hour_with_too_few_samples():
    # Hour 10 has only 2 (extreme) errors, so the all-hours spread is used instead.
    history = pd.DataFrame({"hour": [3] * 10 + [10, 10], "error_mw": [0.0] * 10 + [4000.0, 5000.0]})
    overall = history["error_mw"].quantile([0.1, 0.5, 0.9]).to_numpy()
    banded = an.add_uncertainty_band(_day_at_ten(8000.0), history)
    assert banded["expected_mw"].iloc[0] == pytest.approx(8000 - overall[1])
    assert banded["low_mw"].iloc[0] == pytest.approx(8000 - overall[2])


def test_no_band_from_too_little_history():
    history = pd.DataFrame({"hour": [10, 10], "error_mw": [0.0, 100.0]})
    banded = an.add_uncertainty_band(_day_at_ten(8000.0), history)
    assert banded[["expected_mw", "low_mw", "high_mw"]].isna().all(axis=None)


# ---- lead time and runs -----------------------------------------------------------------


def test_lead_time_buckets_and_skips_forecasts_made_after_the_target():
    fc = forecasts(
        [
            ("2026-01-15T09:30", "2026-01-15T10:00", 1100),  # 0.5h ahead -> bucket 0
            ("2026-01-14T08:00", "2026-01-15T10:00", 1300),  # 26h ahead  -> bucket 26
            ("2026-01-15T10:30", "2026-01-15T10:00", 999),  # made after target -> skipped
        ]
    )
    act = actuals([("2026-01-15T10:00", 1000)])
    rows = an.error_by_lead_time(fc, act, DAY, DAY)
    assert [(r["lead_hours"], r["mae_mw"]) for r in rows] == [(0, 100), (26, 300)]


def test_runs_for_day_flags_runs_after_cutoff():
    fc = forecasts(
        [
            ("2026-01-14T08:30", "2026-01-15T10:00", 2000),
            ("2026-01-14T14:30", "2026-01-15T10:00", 2500),
            ("2026-01-12T08:30", "2026-01-15T10:00", 1000),  # too old to show
        ]
    )
    runs = an.runs_for_day(fc, DAY, CUTOFF)
    assert [r["after_cutoff"] for r in runs] == [False, True]
    assert runs[1]["points"][0]["forecast_mw"] == 2500


def test_runs_for_day_drop_hours_already_past_at_publication():
    fc = forecasts(
        [
            ("2026-01-15T12:30", "2026-01-15T10:00", 900),  # backfilled past hour
            ("2026-01-15T12:30", "2026-01-15T13:00", 950),
        ]
    )
    runs = an.runs_for_day(fc, DAY, CUTOFF)
    assert [p["forecast_mw"] for p in runs[0]["points"]] == [950]
