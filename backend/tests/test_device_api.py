"""Device API end to end: ask -> proposal on the device -> hold to approve -> the relay follows
the replay clock through the night."""

import hashlib
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_agent_api import Planner
from test_agent_runner import GOOD, PLAN_CALL, say
from wind_fixtures import NOW, synthetic_wind

from wind_monitor import agent_api, planning_api
from wind_monitor.agent.runs import RunLog, get_run_log
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.device import DeviceRegistry, get_registry
from wind_monitor.firmware import get_firmware, load_image
from wind_monitor.main import create_app

QUESTION = "I need 14 kWh at 7 kW, when should I charge?"


@pytest.fixture
def clock() -> ReplayClock:
    return ReplayClock(NOW)


@pytest.fixture
def client(clock) -> TestClient:
    data, log, registry = synthetic_wind(), RunLog(), DeviceRegistry()
    planner = Planner(PLAN_CALL, say(GOOD), say("I can't plan that."))
    app = create_app(replace(Settings.from_env(), static_dir=None))
    app.dependency_overrides[planning_api.planning_data] = lambda: data
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[agent_api.get_planner] = lambda: planner
    app.dependency_overrides[get_run_log] = lambda: log
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_firmware] = lambda: None
    return TestClient(app)


def _state(client, device="desk-1", **headers):
    return client.get(f"/api/device/{device}/state", headers=headers)


def test_a_new_device_shows_the_wind_now(client):
    body = _state(client).json()
    assert body["mode"] == "idle" and body["relay"] is False
    assert body["lines"][:2] == ["10.0 GW", "at 17:00"]
    assert body["now_uk"] == "18:00" and body["poll_seconds"] == 5


def test_full_night(client, clock):
    run = client.post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"}).json()
    assert run["proposed_to"] == "desk-1"

    body = _state(client).json()
    assert body["mode"] == "proposal" and body["proposal_id"] == run["id"]
    assert body["lines"][0] == "02:00-04:00 14kWh" and body["prompt"] == "hold:yes  tap:no"

    approve = {"type": "approve", "proposal_id": run["id"]}
    body = client.post("/api/device/desk-1/event", json=approve).json()
    assert body["mode"] == "waiting" and body["relay"] is False

    clock.set(at=NOW.replace(day=9, hour=2, minute=15))
    body = _state(client).json()
    assert body["mode"] == "charging" and body["relay"] is True
    assert body["lines"][0] == "until 04:00"

    clock.set(at=NOW.replace(day=9, hour=4))
    body = _state(client).json()
    assert body["mode"] == "done" and body["relay"] is False

    listed = client.get("/api/devices").json()
    assert listed[0]["device_id"] == "desk-1" and listed[0]["active_id"] == run["id"]


def test_an_answer_without_a_plan_is_not_sent(client):
    client.post("/api/agent/ask", json={"question": QUESTION})  # uses the first two replies
    run = client.post("/api/agent/ask", json={"question": "hi", "device_id": "desk-1"}).json()
    assert run["proposed_to"] is None
    assert _state(client).json()["mode"] == "idle"


def test_propose_an_earlier_run_by_id(client):
    run = client.post("/api/agent/ask", json={"question": QUESTION}).json()
    body = client.post("/api/device/porch/propose", json={"run_id": run["id"]}).json()
    assert body["mode"] == "proposal" and body["device_id"] == "porch"
    assert client.post("/api/device/porch/propose", json={"run_id": "nope"}).status_code == 404


def test_stale_approval_is_a_conflict(client):
    response = client.post("/api/device/desk-1/event", json={"type": "approve", "proposal_id": "x"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("path", "payload"),
    [("/api/device/Desk_1/state", None), ("/api/device/desk-1/event", {"type": "dance"})],
)
def test_bad_ids_and_events_are_rejected(client, path, payload):
    response = client.post(path, json=payload) if payload else client.get(path)
    assert response.status_code == 422


def test_device_tokens(client, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKENS", "desk-1:s3cret, porch:other")
    assert _state(client).status_code == 401
    assert _state(client, **{"X-Device-Token": "wrong"}).status_code == 401
    assert _state(client, **{"X-Device-Token": "s3cret"}).status_code == 200
    assert _state(client, device="garage", **{"X-Device-Token": "s3cret"}).status_code == 401


def test_telemetry_is_kept(client):
    event = {"type": "telemetry", "relay": False, "rssi": -58, "uptime_s": 120, "firmware": "0.1"}
    assert client.post("/api/device/desk-1/event", json=event).status_code == 200
    assert client.get("/api/devices").json()[0]["telemetry"]["rssi"] == -58


def test_remote_relay_test_needs_the_app_token(client, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "letmein")
    assert client.post("/api/device/desk-1/test", json={"seconds": 5}).status_code == 401
    body = client.post(
        "/api/device/desk-1/test", json={"seconds": 5}, headers={"X-App-Token": "letmein"}
    ).json()
    assert body["relay"] is True and body["mode"] == "test"
    assert (
        client.post(
            "/api/device/desk-1/test", json={"seconds": 500}, headers={"X-App-Token": "letmein"}
        ).status_code
        == 422
    )


def test_rich_telemetry_is_kept(client):
    event = {
        "type": "telemetry",
        "relay": True,
        "relay_pin": True,
        "free_heap": 182_000,
        "last_http": 200,
        "mode": "charging",
        "version": 4,
        "presses": 2,
        "ip": "192.168.1.40",
    }
    client.post("/api/device/desk-1/event", json=event)
    telemetry = client.get("/api/devices").json()[0]["telemetry"]
    assert telemetry["relay_pin"] is True and telemetry["presses"] == 2
    assert telemetry["last_http"] == 200 and telemetry["mode"] == "charging"


def _write_image(folder, version="0.3.0", data=b"\xe9 firmware bytes", **manifest):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "windbuddy-app.bin").write_bytes(data)
    info = {
        "version": version,
        "app": "windbuddy-app.bin",
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
    }
    (folder / "manifest.json").write_text(json.dumps({**info, **manifest}))
    return load_image(folder)


def test_over_the_air_update(client, tmp_path):
    image = _write_image(tmp_path)
    client.app.dependency_overrides[get_firmware] = lambda: image
    assert client.get("/api/firmware").json()["version"] == "0.3.0"
    assert _state(client).json()["update"] is None  # nothing is offered until asked

    asked = client.post("/api/device/desk-1/update", json={"version": "0.3.0"}).json()
    offer = asked["update"]
    assert offer["path"] == "/api/device/desk-1/firmware" and offer["md5"] == image.md5
    assert _state(client).json()["update"] == offer
    assert client.get("/api/devices").json()[0]["update_to"] == "0.3.0"

    download = client.get(offer["path"])
    assert download.status_code == 200 and download.content == b"\xe9 firmware bytes"
    assert download.headers["x-firmware-md5"] == image.md5
    assert int(download.headers["content-length"]) == offer["size"]

    # The device restarts into the new firmware and says so: the offer is withdrawn.
    client.post("/api/device/desk-1/event", json={"type": "telemetry", "firmware": "0.3.0"})
    assert _state(client).json()["update"] is None
    listed = client.get("/api/devices").json()[0]
    assert listed["update_to"] is None and listed["events"][0]["event"] == "updated to 0.3.0"


def test_update_guards(client, tmp_path, monkeypatch):
    assert client.post("/api/device/desk-1/update", json={}).status_code == 404  # no image
    image = _write_image(tmp_path)
    client.app.dependency_overrides[get_firmware] = lambda: image
    wrong = client.post("/api/device/desk-1/update", json={"version": "9.9"})
    assert wrong.status_code == 409

    client.post("/api/device/desk-1/update", json={})
    cancelled = client.post("/api/device/desk-1/update", json={"cancel": True}).json()
    assert cancelled["update"] is None

    monkeypatch.setenv("APP_TOKEN", "letmein")
    assert client.post("/api/device/desk-1/update", json={}).status_code == 401
    monkeypatch.setenv("DEVICE_TOKENS", "desk-1:s3cret")
    assert client.get("/api/device/desk-1/firmware").status_code == 401
    ok = client.get("/api/device/desk-1/firmware", headers={"X-Device-Token": "s3cret"})
    assert ok.status_code == 200


def test_a_device_already_running_the_version_is_not_offered_it(client, tmp_path):
    image = _write_image(tmp_path)
    client.app.dependency_overrides[get_firmware] = lambda: image
    client.post("/api/device/desk-1/event", json={"type": "telemetry", "firmware": "0.3.0"})
    client.post("/api/device/desk-1/update", json={})
    assert _state(client).json()["update"] is None


@pytest.mark.parametrize(
    "manifest",
    [{"md5": "0" * 32}, {"size": 3}, {"app": "missing.bin"}],
)
def test_a_broken_image_is_never_offered(tmp_path, manifest):
    assert _write_image(tmp_path, **manifest) is None
    assert load_image(tmp_path / "nowhere") is None


def test_logs_arrive_with_any_event(client):
    lines = ["[12s] boot 0.3.0 (power on)", "[13s] wifi ok -61 dBm"]
    client.post("/api/device/desk-1/event", json={"type": "telemetry", "log": lines})
    listed = client.get("/api/devices").json()[0]
    assert [entry["line"] for entry in listed["logs"]] == lines
    assert "log" not in listed["telemetry"]
    client.post("/api/device/desk-1/event", json={"type": "stop", "log": ["[20s] hold"]})
    logs = client.get("/api/device/desk-1/logs").json()
    assert [entry["line"] for entry in logs] == [*lines, "[20s] hold"]
    assert client.get("/api/device/porch/logs").status_code == 404


def test_log_lines_are_bounded(client):
    too_long = {"type": "telemetry", "log": ["x" * 201]}
    too_many = {"type": "telemetry", "log": ["x"] * 51}
    for event in (too_long, too_many):
        assert client.post("/api/device/desk-1/event", json=event).status_code == 422
