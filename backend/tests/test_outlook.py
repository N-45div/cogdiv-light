"""What the planner may know at a moment T: published runs only, settled actuals only, and an
error band that depends on how far ahead each forecast was made.

Fixture: actual wind is a flat 10,000 MW. A run is published every 6 hours and covers the next
48 hours; its error depends only on how far ahead it looks: +100 MW under 6 h, +300 MW at 6-12 h,
+600 MW at 12-24 h and +1,000 MW beyond. So every bias-adjusted value should come back to
exactly 10,000 MW, whatever the lead time.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from wind_monitor import outlook as ol
from wind_monitor.store import WindData

ACTUAL_MW = 10_000.0
FIRST = pd.Timestamp("2026-01-01T00:00Z")


def _offset(lead_hours: float) -> float:
    if lead_hours < 6:
        return 100.0
    if lead_hours < 12:
        return 300.0
    if lead_hours < 24:
        return 600.0
    return 1000.0


def _data(days: int = 10) -> WindData:
    halves = pd.date_range(FIRST, periods=48 * days, freq="30min")
    actuals = pd.DataFrame({"start_time": halves, "generation_mw": ACTUAL_MW})
    rows = []
    for published in pd.date_range(FIRST - pd.Timedelta(days=1), periods=4 * (days + 1), freq="6h"):
        for h in range(1, 49):
            rows.append((published, published + pd.Timedelta(hours=h), ACTUAL_MW + _offset(h)))
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


def test_actuals_count_only_once_published():
    data = _data()
    as_of = pd.Timestamp("2026-01-05T10:00Z")
    known = ol.known_actuals(data.actuals, as_of)
    # 09:00-09:30 ended at 09:30 and is published by 10:00; 09:30-10:00 is not yet.
    assert known["start_time"].max() == pd.Timestamp("2026-01-05T09:00Z")


def test_outlook_uses_only_runs_published_by_then():
    data = _data()
    as_of = pd.Timestamp("2026-01-05T11:59Z")  # the 12:00 run is not out yet
    forecast, _ = ol.outlook(data, as_of, hours=24)
    assert forecast["publish_time"].max() == pd.Timestamp("2026-01-05T06:00Z")
    assert forecast["start_time"].min() == pd.Timestamp("2026-01-05T11:00Z")


def test_band_depends_on_how_far_ahead_the_forecast_was_made():
    data = _data()
    forecast, history = ol.outlook(data, pd.Timestamp("2026-01-08T12:00Z"), hours=48)
    # The raw forecast is +100 MW for the next hours and +1,000 MW for tomorrow evening...
    assert forecast["forecast_mw"].min() == ACTUAL_MW + 100
    assert forecast["forecast_mw"].max() == ACTUAL_MW + 1000
    # ...and correcting each hour by its own lead-time bias brings every hour back to the truth.
    assert np.allclose(forecast["expected_mw"], ACTUAL_MW)
    assert np.allclose(forecast["low_mw"], ACTUAL_MW)
    assert np.allclose(forecast["high_mw"], ACTUAL_MW)
    summary = {row["lead_bucket"]: row for row in ol.history_summary(history)}
    assert summary["0-6h"]["bias_mw"] == 100
    assert summary["24h+"]["bias_mw"] == 1000
    assert summary["24h+"]["over_forecast_share_pct"] == 100


def test_correction_scales_with_the_wind():
    """Calm nights at 4,000 MW, windy days at 16,000 MW, forecast always 25% too high: the gap
    is 1,000 MW when calm and 4,000 MW when windy. Scaling recovers both; one fixed offset
    cannot."""
    halves = pd.date_range(FIRST, periods=48 * 10, freq="30min")
    level = [4_000.0 if t.hour < 12 else 16_000.0 for t in halves]
    actuals = pd.DataFrame({"start_time": halves.as_unit("ns"), "generation_mw": level})
    rows = []
    for published in pd.date_range(FIRST - pd.Timedelta(days=1), periods=44, freq="6h"):
        for h in range(1, 49):
            target = published + pd.Timedelta(hours=h)
            rows.append((published, target, (4_000.0 if target.hour < 12 else 16_000.0) * 1.25))
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for col in ("publish_time", "start_time"):
        forecasts[col] = forecasts[col].dt.as_unit("ns")
    data = WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))

    forecast, history = ol.outlook(data, pd.Timestamp("2026-01-08T00:00Z"), hours=24)
    truth = forecast["start_time"].dt.hour.map(lambda h: 4_000.0 if h < 12 else 16_000.0)
    assert np.allclose(forecast["expected_mw"], truth)
    # The old fixed-offset correction gets both wrong: it subtracts the same amount from both.
    shifted = ol.an.apply_error_band(forecast, forecast["lead_bucket"], history, "lead_bucket")
    assert not np.allclose(shifted["expected_mw"], truth, atol=500)
    assert {row["actual_share_pct"] for row in ol.history_summary(history)} == {80.0}


def test_tiny_forecasts_do_not_teach_ratios():
    """A 10 MW forecast for a 10,000 MW hour would be a ratio of 1,000: such hours are skipped."""
    data = _data()
    tiny = data.forecasts["start_time"].dt.hour == 3
    data.forecasts.loc[tiny, "generation_mw"] = 10.0
    history = ol.error_history(data, pd.Timestamp("2026-01-08T12:00Z"))
    at_3am = history["start_time"].dt.hour == 3
    assert at_3am.any() and history.loc[at_3am, "ratio"].isna().all()
    assert history.loc[~at_3am, "ratio"].notna().all()
    forecast, _ = ol.outlook(data, pd.Timestamp("2026-01-08T12:00Z"), hours=24)
    assert np.allclose(forecast.loc[forecast["forecast_mw"] > 1000, "expected_mw"], ACTUAL_MW)


def test_history_ignores_errors_older_than_the_window():
    data = _data()
    as_of = pd.Timestamp("2026-01-08T12:00Z")
    history = ol.error_history(data, as_of, days=2)
    assert history["start_time"].min() >= as_of - pd.Timedelta(days=2)
    assert history["start_time"].max() < as_of


def test_no_history_means_no_band():
    data = _data()
    # Just after the first actual is published, there is nothing to learn from yet.
    forecast, history = ol.outlook(data, pd.Timestamp("2026-01-01T00:30Z"), hours=6)
    assert history.empty
    assert not forecast.empty
    assert forecast["expected_mw"].isna().all()


def test_recent_tracking_compares_actuals_with_the_freshest_forecast():
    data = _data()
    tracking = ol.recent_tracking(data, pd.Timestamp("2026-01-05T10:00Z"), hours=4)
    # Hourly targets 06:00-09:00 with a published actual. Runs start an hour after publishing,
    # so 06:00 was last forecast by the 00:00 run (6 h ahead, +300 MW); 07:00-09:00 by the
    # 06:00 run (1-3 h ahead, +100 MW).
    assert list(tracking["start_time"].dt.hour) == [6, 7, 8, 9]
    assert list(tracking["lead_hours"]) == [6, 1, 2, 3]
    assert list(tracking["error_mw"]) == [300, 100, 100, 100]


@pytest.mark.parametrize(
    ("lead", "label"), [(-0.5, "0-6h"), (0, "0-6h"), (5.9, "0-6h"), (6, "6-12h"), (30, "24h+")]
)
def test_lead_buckets(lead, label):
    assert ol.lead_bucket(pd.Series([lead])).iloc[0] == label


def test_latest_actual():
    data = _data()
    assert ol.latest_actual(data, pd.Timestamp("2026-01-01T00:59Z")) is None
    latest = ol.latest_actual(data, pd.Timestamp("2026-01-05T10:00Z"))
    assert latest == {"time": datetime(2026, 1, 5, 9, 0, tzinfo=UTC), "actual_mw": ACTUAL_MW}
