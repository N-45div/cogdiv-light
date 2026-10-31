"""The watcher over HTTP, and its background loop on the planning clock."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_watcher import CHECK, EVENING, approved_plan, moved
from wind_fixtures import night_wind

from wind_monitor import planning_api, watch_api
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.device import DeviceRegistry, get_registry
from wind_monitor.main import create_app
from wind_monitor.watcher import Watcher, get_watcher


@pytest.fixture
def world():
    early, late = moved(22, 3)
    data = night_wind(early, late, actual=late)
    registry, watcher = DeviceRegistry(), Watcher()
    approved_plan(data, registry, now=EVENING)
    return data, registry, watcher


@pytest.fixture
def client(world):
    data, registry, watcher = world
    app = create_app(replace(Settings.from_env(), static_dir=None))
    app.dependency_overrides[planning_api.planning_data] = lambda: data
    app.dependency_overrides[get_clock] = lambda: ReplayClock(CHECK)
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_watcher] = lambda: watcher
    app.dependency_overrides[watch_api.get_watch_client] = lambda: None
    return TestClient(app)


def test_run_now_and_read_the_findings(client):
    [finding] = client.post("/api/watch/run").json()
    assert finding["outcome"] == "better_window" and finding["proposal_id"]
    assert client.get("/api/watch").json()[0]["id"] == finding["id"]
    state = client.get("/api/device/desk/state").json()
    assert state["mode"] == "proposal" and state["title"] == "Better window?"


def test_running_the_watcher_needs_the_app_token(client, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "letmein")
    assert client.post("/api/watch/run").status_code == 401
    assert client.post("/api/watch/run", headers={"X-App-Token": "letmein"}).status_code == 200
    assert client.get("/api/watch").status_code == 200  # reading stays open


def test_background_loop_checks_on_the_planning_clock(world, monkeypatch):
    data, registry, watcher = world
    monkeypatch.setattr(watch_api, "LOOP_SECONDS", 0.01)
    monkeypatch.setattr(watch_api, "get_clock", lambda: ReplayClock(CHECK))
    monkeypatch.setattr(watch_api, "get_registry", lambda: registry)
    monkeypatch.setattr(watch_api, "get_watcher", lambda: watcher)
    monkeypatch.setattr(watch_api, "planning_data", lambda clock: data)
    monkeypatch.setattr(watch_api, "get_watch_client", lambda: None)

    async def run_until_found():
        task = asyncio.create_task(watch_api.watch_loop())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if watcher.findings:
                break
        task.cancel()

    asyncio.run(run_until_found())
    assert [f.outcome for f in watcher.findings] == ["better_window"]
    assert watcher.last == CHECK  # and it won't run again until 30 clock-minutes later


def test_watch_setting(monkeypatch):
    assert Settings.from_env().watch is True
    monkeypatch.setenv("WATCH", "0")
    assert Settings.from_env().watch is False
