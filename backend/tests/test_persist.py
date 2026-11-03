"""The server's memory survives a restart: boards, plans, the run log and watcher findings."""

from collections import deque
from dataclasses import replace

import pandas as pd
from fastapi.testclient import TestClient
from test_device import _run

from wind_monitor import persist
from wind_monitor.agent.runs import RunLog
from wind_monitor.config import Settings
from wind_monitor.device import DeviceRegistry
from wind_monitor.main import create_app
from wind_monitor.persist import Persistence
from wind_monitor.watcher import Finding, Watcher

NOW = pd.Timestamp("2026-01-08T18:00Z")


def _world():
    return DeviceRegistry(), RunLog(), Watcher()


def test_a_snapshot_round_trip(tmp_path):
    registry, runs, watcher = _world()
    run = _run("run1")
    runs.add(run)
    runs.add(_run("run2"))
    registry.propose("desk-1", run, NOW)
    registry.event("desk-1", {"type": "approve", "proposal_id": "run1", "log": ["boot ok"]}, NOW)
    watcher.findings.appendleft(Finding("desk-1", NOW.isoformat(), "on_track", {}, "On track"))
    watcher.last = NOW
    keeper = Persistence(tmp_path / "state.pkl", registry, runs, watcher)
    assert keeper.save() is True

    fresh = Persistence(tmp_path / "state.pkl", *_world())
    assert fresh.load() is True
    state = fresh.registry.all()[0]
    assert state.device_id == "desk-1" and state.active.id == "run1"
    assert state.proposal is None and list(state.logs)[0]["line"] == "boot ok"
    assert state.say is None  # the clip itself is gone after a restart
    assert [r.id for r in fresh.runs.recent()] == ["run2", "run1"]  # newest first, as before
    assert fresh.watcher.recent()[0]["message"] == "On track" and fresh.watcher.last == NOW


def test_only_changes_are_written(tmp_path):
    registry, runs, watcher = _world()
    keeper = Persistence(tmp_path / "state.pkl", registry, runs, watcher)
    assert keeper.save() is True
    assert keeper.save() is False
    registry.seen("desk-1", NOW)  # a poll alone isn't worth a write...
    registry.propose("desk-1", _run(), NOW)  # ...a new plan is
    assert keeper.save() is True
    assert keeper.save(force=True) is True


def test_fields_added_since_the_snapshot_get_their_defaults(tmp_path):
    registry, runs, watcher = _world()
    registry.propose("desk-1", _run(), NOW)
    state = registry.all()[0]
    del state.__dict__["logs"]  # as if saved by code from before `logs` existed
    del state.proposal.__dict__["kind"]
    old = Persistence(tmp_path / "state.pkl", registry, runs, watcher)
    (tmp_path / "state.pkl").write_bytes(old.snapshot())

    fresh = Persistence(tmp_path / "state.pkl", *_world())
    assert fresh.load()
    state = fresh.registry.all()[0]
    assert isinstance(state.logs, deque) and state.proposal.kind == "charge"


def test_a_broken_or_missing_snapshot_starts_empty(tmp_path):
    keeper = Persistence(tmp_path / "state.pkl", *_world())
    assert keeper.load() is False
    (tmp_path / "state.pkl").write_bytes(b"not a pickle")
    assert keeper.load() is False and keeper.registry.all() == []


def test_the_app_saves_at_shutdown_and_restores_at_start(tmp_path, monkeypatch):
    settings = replace(
        Settings.from_env(), static_dir=None, watch=False, state_file=tmp_path / "s.pkl"
    )

    def start(registry, runs, watcher) -> TestClient:
        # The app keeps the process-wide registry, run log and watcher: swap in fresh ones.
        monkeypatch.setattr(persist, "get_registry", lambda: registry)
        monkeypatch.setattr(persist, "get_run_log", lambda: runs)
        monkeypatch.setattr(persist, "get_watcher", lambda: watcher)
        return TestClient(create_app(settings))

    registry, runs, watcher = _world()
    with start(registry, runs, watcher):
        registry.propose("desk-1", _run("kept"), NOW)
    assert (tmp_path / "s.pkl").exists()

    registry, runs, watcher = _world()  # a restart: empty memory
    with start(registry, runs, watcher):
        assert registry.all()[0].proposal.id == "kept"
