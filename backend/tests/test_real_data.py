"""The decision rule checked against the real January 2026 snapshot.

The rule is the heart of the product: for each delivery hour, use the latest forecast
published at or before the cut-off on the day before. Here it is re-implemented the slow,
obvious way (scan every forecast for that hour) and compared with what the API returns.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from wind_monitor import config
from wind_monitor.main import create_app
from wind_monitor.store import get_wind_data, load_snapshot

SNAPSHOT = load_snapshot(Path(__file__).resolve().parent.parent / "data")
pytestmark = pytest.mark.skipif(SNAPSHOT is None, reason="no data snapshot in backend/data")

CUTOFF = time(9, 20)


def _uk(day: date, at: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, at, config.UK_TZ)).tz_convert("UTC")


def _days() -> list[date]:
    n = (config.DELIVERY_LAST - config.DELIVERY_FIRST).days + 1
    return [config.DELIVERY_FIRST + timedelta(days=i) for i in range(n)]


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_wind_data] = lambda: SNAPSHOT
    return TestClient(app)


def test_every_hour_uses_the_latest_forecast_published_by_the_cutoff(client):
    forecasts = SNAPSHOT.forecasts
    for day in _days():
        cutoff = _uk(day - timedelta(days=1), CUTOFF)
        points = client.get("/api/day", params={"delivery_date": day.isoformat()}).json()["points"]
        by_time = {p["time"]: p["forecast_mw"] for p in points}
        for hour in range(24):
            target = _uk(day, time(hour))
            candidates = forecasts[
                (forecasts["start_time"] == target) & (forecasts["publish_time"] <= cutoff)
            ]
            expected = candidates.loc[candidates["publish_time"].idxmax(), "generation_mw"]
            assert by_time[target.strftime("%Y-%m-%dT%H:%M:%SZ")] == pytest.approx(expected), (
                f"{day} {hour:02d}:00"
            )


def test_no_forecast_published_after_the_cutoff_is_ever_used(client):
    for day in _days():
        body = client.get("/api/day", params={"delivery_date": day.isoformat()}).json()
        published = pd.Timestamp(body["decision_run"]["publish_time"])
        assert published <= _uk(day - timedelta(days=1), CUTOFF)


def test_horizon_and_headline_accuracy_for_january(client):
    body = client.get("/api/accuracy", params={"cutoff": "09:20"}).json()
    assert body["summary"]["n"] == 31 * 24
    assert body["decision_lead_hours"]["min"] == pytest.approx(16.5)
    assert body["decision_lead_hours"]["max"] == pytest.approx(39.5)
    assert body["summary"]["mae_mw"] == pytest.approx(2022, abs=1)
