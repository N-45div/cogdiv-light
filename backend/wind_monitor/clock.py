"""The planning clock: real time in live mode, or a movable time inside January 2026 in replay.

Everything that plans or watches asks the clock for "now" instead of reading the system time,
so the same code runs live, in a fast-forward demo, and in a backtest.
"""

from __future__ import annotations

import threading
import time as systime
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

import pandas as pd

from . import config


def as_utc(moment: datetime | pd.Timestamp) -> pd.Timestamp:
    """A UTC timestamp. A time without a zone is taken as UK local time (how the desk works)."""
    stamp = pd.Timestamp(moment)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(config.UK_TZ)
    return stamp.tz_convert("UTC")


class Clock(Protocol):
    mode: str

    def now(self) -> pd.Timestamp: ...

    def state(self) -> dict: ...


class LiveClock:
    mode = "live"

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC").floor("s")

    def state(self) -> dict:
        return {"mode": self.mode, "now": self.now(), "speed": 1.0, "first": None, "last": None}


class ReplayClock:
    """A simulated time that is paused (speed 0) or runs `speed` times faster than real time.

    It never leaves [first, last]: at `last` it simply stops.
    """

    mode = "replay"

    def __init__(
        self,
        start: datetime | pd.Timestamp,
        first: datetime | pd.Timestamp = config.REPLAY_FIRST,
        last: datetime | pd.Timestamp = config.REPLAY_LAST,
        speed: float = 0.0,
        wall: Callable[[], float] = systime.monotonic,
    ) -> None:
        self.first, self.last = as_utc(first), as_utc(last)
        self._wall = wall
        self._lock = threading.Lock()
        self._anchor_sim = self._checked(as_utc(start))
        self._anchor_wall = wall()
        self._speed = self._checked_speed(speed)

    def now(self) -> pd.Timestamp:
        with self._lock:
            return self._now()

    def set(self, at: datetime | pd.Timestamp | None = None, speed: float | None = None) -> None:
        """Jump to `at` and/or change speed. Raises ValueError outside the replay window."""
        with self._lock:
            new_anchor = self._checked(as_utc(at)) if at is not None else self._now()
            new_speed = self._checked_speed(speed) if speed is not None else self._speed
            self._anchor_sim, self._anchor_wall, self._speed = new_anchor, self._wall(), new_speed

    def advance(self, delta: pd.Timedelta) -> None:
        """Move forward (or back) by `delta`, stopping at the window edges."""
        with self._lock:
            target = min(max(self._now() + delta, self.first), self.last)
            self._anchor_sim, self._anchor_wall = target, self._wall()

    def state(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "now": self._now(),
                "speed": self._speed,
                "first": self.first,
                "last": self.last,
            }

    def _now(self) -> pd.Timestamp:
        elapsed = (self._wall() - self._anchor_wall) * self._speed
        moment = self._anchor_sim + pd.Timedelta(seconds=elapsed)
        return min(moment, self.last).floor("s")

    def _checked(self, moment: pd.Timestamp) -> pd.Timestamp:
        if not self.first <= moment <= self.last:
            raise ValueError(f"Replay time must be between {self.first} and {self.last}")
        return moment

    @staticmethod
    def _checked_speed(speed: float) -> float:
        if not 0 <= speed <= MAX_REPLAY_SPEED:
            raise ValueError(f"Replay speed must be between 0 and {MAX_REPLAY_SPEED}")
        return float(speed)


# 10,000x: one simulated week in about a minute of real time.
MAX_REPLAY_SPEED = 10_000.0


class SwitchableClock:
    """Live or replay, switchable while running (POST /api/clock {"mode": ...}).

    The replay keeps its position while live is on, so switching back resumes where it was.
    Moving the clock (set / advance) always moves the replay.
    """

    def __init__(self, replay: ReplayClock, mode: str = "replay", live: LiveClock | None = None):
        self.replay, self.live = replay, live or LiveClock()
        self._mode = self._checked_mode(mode)

    @property
    def mode(self) -> str:
        return self._mode

    def switch(self, mode: str) -> None:
        self._mode = self._checked_mode(mode)

    def now(self) -> pd.Timestamp:
        return self._active().now()

    def state(self) -> dict:
        return self._active().state()

    def set(self, at: datetime | pd.Timestamp | None = None, speed: float | None = None) -> None:
        self.replay.set(at=at, speed=speed)

    def advance(self, delta: pd.Timedelta) -> None:
        self.replay.advance(delta)

    def _active(self) -> Clock:
        return self.live if self._mode == "live" else self.replay

    @staticmethod
    def _checked_mode(mode: str) -> str:
        if mode not in config.CLOCK_MODES:
            raise ValueError(f"Clock mode must be one of {config.CLOCK_MODES}")
        return mode


def make_clock(settings: config.Settings) -> SwitchableClock:
    replay = ReplayClock(settings.replay_start, speed=settings.replay_speed)
    return SwitchableClock(replay, settings.clock_mode)


# One clock per process, created on first use.
_clock: Clock | None = None
_clock_lock = threading.Lock()


def get_clock() -> Clock:
    global _clock
    if _clock is None:
        with _clock_lock:
            if _clock is None:
                _clock = make_clock(config.Settings.from_env())
    return _clock
