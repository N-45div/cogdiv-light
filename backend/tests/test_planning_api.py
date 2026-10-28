"""End-to-end tests of the planning API: /api/clock, /api/outlook, /api/plan.

Fixture: actual wind is 10,000 MW, except 02:00-04:00 UTC every night when it is 15,000 MW.
A run is published every 6 hours for the next 48 hours, too high by 100 MW under 6 h ahead and
by 1,000 MW further out. The clock is a paused replay at 18:00 on 8 Jan.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from wind_monitor import planning_api
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.main import create_app
from wind_monitor.store import WindData

FIRST = pd.Timestamp("2026-01-01T00:00Z")
NOW = pd.Timestamp("2026-01-08T18:00Z")


def _actual(moment: pd.Timestamp) -> float:
    return 15_000.0 if moment.hour in (2, 3) else 10_000.0


def _data() -> WindData:
    halves = pd.date_range(FIRST, periods=48 * 12, freq="30min")
    actuals = pd.DataFrame({"start_time": halves, "generation_mw": [_actual(t) for t in halves]})
    rows = []
    for published in pd.date_range(FIRST - pd.Timedelta(days=1), periods=4 * 13, freq="6h"):
        for h in range(1, 49):
            target = published + pd.Timedelta(hours=h)
            rows.append((published, target, _actual(target) + (100 if h < 6 else 1000)))
    forecasts = pd.DataFrame(rows, columns=["publish_time", "start_time", "generation_mw"])
    for df in (actuals, forecasts):
        for col in df.columns:
            if col.endswith("_time"):
                df[col] = df[col].dt.as_unit("ns")
    return WindData(actuals, forecasts, "snapshot", datetime(2026, 10, 1, tzinfo=UTC))


@pytest.fixture
def clock() -> ReplayClock:
    return ReplayClock(NOW)


@pytest.fixture
def client(clock) -> TestClient:
    data = _data()
    app = create_app(replace(Settings.from_env(), static_dir=None))
    app.dependency_overrides[planning_api.planning_data] = lambda: data
    app.dependency_overrides[get_clock] = lambda: clock
    return TestClient(app)


# ---- clock ------------------------------------------------------------------------------


def test_clock_reports_replay_state(client):
    body = client.get("/api/clock").json()
    assert body["mode"] == "replay"
    assert body["now"] == "2026-01-08T18:00:00Z"
    assert body["speed"] == 0


def test_clock_can_jump_and_step(client):
    assert client.post("/api/clock", json={"at": "2026-01-10T09:00"}).json()["now"] == (
        "2026-01-10T09:00:00Z"
    )
    body = client.post("/api/clock", json={"advance_minutes": 90}).json()
    assert body["now"] == "2026-01-10T10:30:00Z"


@pytest.mark.parametrize("update", [{"at": "2025-06-01T00:00"}, {"speed": -1}])
def test_clock_rejects_bad_moves(client, update):
    assert client.post("/api/clock", json=update).status_code == 422
    assert client.get("/api/clock").json()["now"] == "2026-01-08T18:00:00Z"


def test_live_clock_cannot_be_moved(client):
    class Live:
        mode = "live"

    client.app.dependency_overrides[get_clock] = lambda: Live()
    assert client.post("/api/clock", json={"speed": 10}).status_code == 409


# ---- outlook ----------------------------------------------------------------------------


def test_outlook_is_as_of_the_clock(client, clock):
    body = client.get("/api/outlook", params={"hours": 12}).json()
    assert body["as_of"] == "2026-01-08T18:00:00Z"
    assert body["latest_run"] == "2026-01-08T18:00:00Z"
    assert body["latest_actual"] == {"time": "2026-01-08T17:00:00Z", "actual_mw": 10_000.0}
    assert body["points"][0]["time"] == "2026-01-08T18:00:00Z"
    assert all(p["publish_time"] <= body["as_of"] for p in body["points"])
    # Each hour corrected by its own lead-time bias lands on the true value.
    for point in body["points"]:
        assert point["expected_mw"] == _actual(pd.Timestamp(point["time"]))

    clock.set(at=pd.Timestamp("2026-01-08T23:59Z"))  # the midnight run is not out yet
    assert client.get("/api/outlook").json()["latest_run"] == "2026-01-08T18:00:00Z"


def test_outlook_reports_history_and_recent_tracking(client):
    body = client.get("/api/outlook", params={"recent_hours": 3}).json()
    by_lead = {row["lead_bucket"]: row for row in body["history"]["by_lead"]}
    assert by_lead["0-6h"]["bias_mw"] == 100
    assert by_lead["24h+"]["bias_mw"] == 1000
    assert body["history"]["n"] > 0 and "median past error" in body["band_note"]
    assert body["recent"]["mean_error_mw"] == 100
    assert [p["time"][11:16] for p in body["recent"]["points"]] == ["15:00", "16:00", "17:00"]


# ---- plans ------------------------------------------------------------------------------


def _blocks(body) -> list[tuple[str, str]]:
    return [(b["start"], b["end"]) for b in body["blocks"]]


def test_plan_goes_in_the_windy_hours_by_the_default_deadline(client):
    body = client.post("/api/plan", json={"energy_kwh": 14, "power_kw": 7}).json()
    assert body["job"]["earliest"] == "2026-01-08T18:00:00Z"
    assert body["job"]["deadline"] == "2026-01-09T07:00:00Z"  # next 07:00 UK time
    assert body["feasible"] and body["planned_kwh"] == 14
    assert _blocks(body) == [("2026-01-09T02:00:00Z", "2026-01-09T04:00:00Z")]
    assert body["wind"]["planned"]["expected_mw"] == 15_000
    assert body["wind"]["asap"]["expected_mw"] == 10_000
    assert body["forecast_run"] == "2026-01-08T18:00:00Z"
    assert body["notes"] == []
    assert len(body["slots"]) == 26 and sum(s["on"] for s in body["slots"]) == 4


def test_plan_never_starts_in_the_past(client):
    job = {"energy_kwh": 7, "power_kw": 7, "earliest": "2026-01-08T12:00", "interruptible": False}
    body = client.post("/api/plan", json=job).json()
    assert body["job"]["earliest"] == "2026-01-08T18:00:00Z"
    assert body["slots"][0]["time"] == "2026-01-08T18:00:00Z"


def test_plan_reports_a_job_that_cannot_finish(client):
    body = client.post(
        "/api/plan", json={"energy_kwh": 50, "power_kw": 7, "deadline": "2026-01-08T21:00"}
    ).json()
    assert not body["feasible"]
    assert body["shortfall_kwh"] == 29
    assert "cannot finish" in body["notes"][0]


@pytest.mark.parametrize(
    "job",
    [
        {"energy_kwh": 7, "power_kw": 7, "deadline": "2026-01-08T17:00"},  # before now
        {"energy_kwh": 7, "power_kw": 7, "deadline": "2026-01-11T07:00"},  # too far ahead
        {"energy_kwh": 7, "power_kw": 0},
        {"energy_kwh": 7, "power_kw": 7, "basis": "hopeful"},
    ],
)
def test_plan_rejects_bad_jobs(client, job):
    assert client.post("/api/plan", json=job).status_code == 422


def test_replay_plans_on_the_january_data_and_live_on_the_live_window(monkeypatch):
    january, live = _data(), replace(_data(), source="live")

    class Window:
        def get(self, now):
            assert now == NOW
            return live

    class Live:
        mode = "live"

        def now(self):
            return NOW

    monkeypatch.setattr(planning_api, "get_wind_data", lambda: january)
    monkeypatch.setattr(planning_api, "get_live_window", lambda: Window())
    assert planning_api.planning_data(ReplayClock(NOW)) is january
    assert planning_api.planning_data(Live()) is live


def test_live_outlook_reports_live_mode(client):
    class Live:
        mode = "live"

        def now(self):
            return NOW

    client.app.dependency_overrides[get_clock] = lambda: Live()
    body = client.get("/api/outlook").json()
    assert body["mode"] == "live" and body["as_of"] == "2026-01-08T18:00:00Z"
