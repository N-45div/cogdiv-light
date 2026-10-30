"""The planning clock: replay moves only when told to, at the speed asked, inside January."""

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from wind_monitor import config
from wind_monitor.clock import LiveClock, ReplayClock, SwitchableClock, as_utc, make_clock

START = pd.Timestamp("2026-01-14T09:00Z")


class FakeWall:
    """A wall clock the test moves by hand."""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds


def test_naive_times_are_uk_local_and_aware_times_are_kept():
    # 14 Jan is GMT (= UTC); 1 Jul is BST (UTC+1).
    assert as_utc(datetime(2026, 1, 14, 9, 0)) == pd.Timestamp("2026-01-14T09:00Z")
    assert as_utc(datetime(2026, 7, 1, 9, 0)) == pd.Timestamp("2026-07-01T08:00Z")
    assert as_utc(pd.Timestamp("2026-07-01T09:00Z")) == pd.Timestamp("2026-07-01T09:00Z")


def test_paused_replay_does_not_move():
    wall = FakeWall()
    clock = ReplayClock(START, wall=wall)
    wall.seconds += 3600
    assert clock.now() == START


def test_replay_runs_at_the_chosen_speed():
    wall = FakeWall()
    clock = ReplayClock(START, speed=60, wall=wall)  # one simulated minute per real second
    wall.seconds += 30
    assert clock.now() == START + pd.Timedelta(minutes=30)


def test_changing_speed_keeps_the_current_time():
    wall = FakeWall()
    clock = ReplayClock(START, speed=60, wall=wall)
    wall.seconds += 10
    clock.set(speed=0)
    wall.seconds += 100
    assert clock.now() == START + pd.Timedelta(minutes=10)


def test_jump_and_advance():
    wall = FakeWall()
    clock = ReplayClock(START, wall=wall)
    clock.set(at=pd.Timestamp("2026-01-20T18:00Z"))
    clock.advance(pd.Timedelta(hours=2))
    assert clock.now() == pd.Timestamp("2026-01-20T20:00Z")


def test_replay_stops_at_the_end_of_the_window():
    wall = FakeWall()
    clock = ReplayClock(START, speed=10_000, wall=wall)
    wall.seconds += 10_000_000
    assert clock.now() == as_utc(config.REPLAY_LAST)
    clock.advance(pd.Timedelta(days=-100))
    assert clock.now() == as_utc(config.REPLAY_FIRST)


@pytest.mark.parametrize(
    ("at", "speed"),
    [
        (pd.Timestamp("2025-12-31T23:00Z"), None),
        (pd.Timestamp("2026-03-01T00:00Z"), None),
        (None, -1),
    ],
)
def test_replay_rejects_times_and_speeds_outside_the_limits(at, speed):
    clock = ReplayClock(START, wall=FakeWall())
    with pytest.raises(ValueError):
        clock.set(at=at, speed=speed)
    assert clock.now() == START  # a rejected change leaves the clock as it was


def test_settings_pick_the_starting_mode():
    settings = config.Settings.from_env()
    assert make_clock(replace(settings, clock_mode="live")).mode == "live"
    replay = make_clock(replace(settings, clock_mode="replay", replay_start=START))
    assert replay.mode == "replay"
    assert replay.now() == START


def test_switching_modes_keeps_the_replay_position():
    clock = SwitchableClock(ReplayClock(START, wall=FakeWall()), "replay")
    clock.advance(pd.Timedelta(hours=2))
    clock.switch("live")
    assert clock.mode == "live" and clock.state()["mode"] == "live"
    assert clock.now() > pd.Timestamp("2026-06-01T00:00Z")  # real time
    clock.switch("replay")
    assert clock.now() == START + pd.Timedelta(hours=2)
    with pytest.raises(ValueError):
        clock.switch("tomorrow")


def test_settings_read_clock_from_environment(monkeypatch):
    monkeypatch.setenv("CLOCK_MODE", "LIVE")
    monkeypatch.setenv("REPLAY_START", "2026-01-20T18:00")
    monkeypatch.setenv("REPLAY_SPEED", "120")
    settings = config.Settings.from_env()
    assert settings.clock_mode == "live"
    assert as_utc(settings.replay_start) == pd.Timestamp("2026-01-20T18:00Z")
    assert settings.replay_speed == 120
    monkeypatch.setenv("CLOCK_MODE", "sometimes")
    with pytest.raises(ValueError):
        config.Settings.from_env()


def test_live_clock_is_real_time():
    before = pd.Timestamp.now(tz="UTC").floor("s")
    assert LiveClock().now() >= before
