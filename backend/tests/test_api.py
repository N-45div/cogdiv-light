"""End-to-end tests of the HTTP API on a small, fully known dataset.

Fixture: delivery days 2-4 Jan. Each day has half-hourly actuals (10,000 MW + 100 MW per hour)
and two forecast runs published the day before: 08:30 (before the 09:20 cut-off, 300 MW too
high) and 14:30 (after the cut-off, 100 MW too high).
"""

from dataclasses import replace
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from wind_monitor.config import Settings
from wind_monitor.main import create_app
from wind_monitor.store import WindData, get_wind_data

DAYS = [date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]


def _actual_mw(moment: pd.Timestamp) -> float:
    return 10_000.0 + 100 * moment.hour


@pytest.fixture
def wind_data() -> WindData:
    actual_rows, forecast_rows = [], []
    for day in DAYS:
        start = pd.Timestamp(day, tz="UTC")
        for i in range(48):
            moment = start + pd.Timedelta(minutes=30 * i)
            actual_rows.append((moment, _actual_mw(moment)))
        for publish_hour, offset in ((8.5, 300.0), (14.5, 100.0)):
            published = start - pd.Timedelta(days=1) + pd.Timedelta(hours=publish_hour)
            for h in range(24):
                moment = start + pd.Timedelta(hours=h)
                forecast_rows.append((published, moment, _actual_mw(moment) + offset))
    actuals = pd.DataFrame(actual_rows, columns=["start_time", "generation_mw"])
    forecasts = pd.DataFrame(forecast_rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


@pytest.fixture
def client(wind_data) -> TestClient:
    app = create_app(replace(Settings.from_env(), static_dir=None))
    app.dependency_overrides[get_wind_data] = lambda: wind_data
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_describes_scope_and_data(client):
    body = client.get("/api/meta").json()
    assert body["delivery_first"] == "2026-01-02"
    assert body["delivery_last"] == "2026-02-01"
    assert body["default_cutoff"] == "09:20"
    assert [p["time"] for p in body["cutoff_presets"]] == ["09:20", "09:50"]
    assert body["data"]["actuals_rows"] == 3 * 48
    assert body["data"]["forecast_runs"] == 6


def test_day_uses_last_run_before_cutoff(client):
    body = client.get("/api/day", params={"delivery_date": "2026-01-03"}).json()

    assert body["decision_date"] == "2026-01-02"
    assert body["decision_run"]["publish_time"] == "2026-01-02T08:30:00Z"
    assert body["decision_run"]["lead_hours_min"] == pytest.approx(15.5)
    assert body["decision_run"]["lead_hours_max"] == pytest.approx(38.5)
    assert len(body["points"]) == 48  # every half-hour actual, forecast on the hour
    assert body["summary"]["accuracy"]["bias_mw"] == pytest.approx(300)
    assert body["summary"]["accuracy"]["n"] == 24
    assert [r["after_cutoff"] for r in body["runs"]] == [False, True]


def test_day_band_comes_from_other_days_errors(client):
    body = client.get("/api/day", params={"delivery_date": "2026-01-03"}).json()
    # Other days were always exactly 300 MW too high, so the band collapses onto the actual.
    on_the_hour = [p for p in body["points"] if p["forecast_mw"] is not None]
    for point in on_the_hour:
        assert point["expected_mw"] == pytest.approx(point["actual_mw"])
        assert point["low_mw"] == pytest.approx(point["actual_mw"])
        assert point["high_mw"] == pytest.approx(point["actual_mw"])
    assert "other 2 delivery days" in body["band_note"]


def test_later_cutoff_uses_later_run(client):
    body = client.get("/api/day", params={"delivery_date": "2026-01-03", "cutoff": "15:00"}).json()
    assert body["decision_run"]["publish_time"] == "2026-01-02T14:30:00Z"
    assert body["summary"]["accuracy"]["bias_mw"] == pytest.approx(100)


def test_day_without_data_still_answers(client):
    body = client.get("/api/day", params={"delivery_date": "2026-01-20"}).json()
    assert body["decision_run"] is None
    assert body["points"] == []
    assert body["summary"]["accuracy"]["n"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"delivery_date": "2026-01-01"},  # decision on 31 Dec: out of scope
        {"delivery_date": "2026-02-02"},
        {"delivery_date": "2026-01-03", "cutoff": "9:20"},
        {"delivery_date": "2026-01-03", "cutoff": "24:00"},
        {"delivery_date": "not-a-date"},
    ],
)
def test_day_rejects_bad_input(client, params):
    assert client.get("/api/day", params=params).status_code == 422


def test_accuracy_over_all_days(client):
    body = client.get("/api/accuracy").json()
    assert body["summary"]["n"] == 3 * 24
    assert body["summary"]["mae_mw"] == pytest.approx(300)
    assert body["summary"]["bias_mw"] == pytest.approx(300)
    assert [d["delivery_date"] for d in body["daily"]] == ["2026-01-02", "2026-01-03", "2026-01-04"]
    assert len(body["by_hour"]) == 24
    assert body["decision_lead_hours"] == {"min": pytest.approx(15.5), "max": pytest.approx(38.5)}
    assert sum(b["count"] for b in body["histogram"]) == 72
    # lead-time view uses every run: 08:30 runs are 15.5-38.5h ahead, 14:30 runs 9.5-32.5h
    leads = {row["lead_hours"]: row for row in body["by_lead_time"]}
    assert leads[9]["mae_mw"] == pytest.approx(100)
    assert leads[38]["mae_mw"] == pytest.approx(300)


def test_data_endpoints_are_cacheable(client):
    response = client.get("/api/accuracy")
    assert "s-maxage" in response.headers["cache-control"]
    assert "cache-control" not in client.get("/api/health").headers


def test_serves_frontend_when_static_dir_is_set(tmp_path, wind_data):
    (tmp_path / "index.html").write_text("<h1>wind</h1>")
    app = create_app(replace(Settings.from_env(), static_dir=tmp_path))
    app.dependency_overrides[get_wind_data] = lambda: wind_data
    client = TestClient(app)
    assert "wind" in client.get("/").text
    assert client.get("/api/health").status_code == 200


def test_openapi_docs_are_published(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
