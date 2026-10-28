"""Business rules and runtime settings, kept in one place so they are easy to review."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# GB trading runs on UK local time (GMT in winter, BST in summer).
UK_TZ = ZoneInfo("Europe/London")

# Scope from the brief: purchase decisions made in January 2026.
# A day-ahead decision on day D-1 buys power for delivery day D,
# so decisions on 1-31 Jan cover delivery days 2 Jan - 1 Feb.
DECISION_FIRST = date(2026, 1, 1)
DECISION_LAST = date(2026, 1, 31)
DELIVERY_FIRST = DECISION_FIRST + timedelta(days=1)
DELIVERY_LAST = DECISION_LAST + timedelta(days=1)


@dataclass(frozen=True)
class CutoffPreset:
    key: str
    label: str
    time: time


# Gate closure times of the GB day-ahead auctions (UK local time, day before delivery).
CUTOFF_PRESETS: tuple[CutoffPreset, ...] = (
    CutoffPreset("epex", "EPEX SPOT GB day-ahead auction (09:20)", time(9, 20)),
    CutoffPreset("n2ex", "Nord Pool N2EX GB day-ahead auction (09:50)", time(9, 50)),
)
DEFAULT_CUTOFF = CUTOFF_PRESETS[0].time

# How far back/forward we pull data around the decision window:
# forecasts are published up to ~2 days ahead, so start 2 days before the first decision,
# and keep forecasts published up to the end of the last delivery day (for "later runs").
FORECAST_PUBLISH_FROM = datetime.combine(DECISION_FIRST - timedelta(days=2), time(0), UK_TZ)
FORECAST_PUBLISH_TO = datetime.combine(DELIVERY_LAST + timedelta(days=1), time(0), UK_TZ)
ACTUALS_FROM = DECISION_FIRST
ACTUALS_TO = DELIVERY_LAST

# Lead-time analysis looks at forecasts made 0-48 hours before the target time.
MAX_LEAD_HOURS = 48

# ---- the clock: live, or replaying January 2026 -------------------------------------
# Planning works "as of" a moment: only forecasts published and actuals settled by then count.
# In live mode that moment is now; in replay mode it moves through the January snapshot, which
# gives a reproducible demo and a backtest with no look-ahead.
CLOCK_MODES = ("replay", "live")
# Replay can move from the first decision day to the end of the last delivery day.
REPLAY_FIRST = datetime.combine(DECISION_FIRST, time(0), UK_TZ)
REPLAY_LAST = datetime.combine(DELIVERY_LAST + timedelta(days=1), time(0), UK_TZ)
# Default replay moment: 09:00 on 14 Jan, just before the cut-off for 15 Jan delivery
# (the worked example in docs/DECISIONS.md, A3).
DEFAULT_REPLAY_START = datetime(2026, 1, 14, 9, 0, tzinfo=UK_TZ)

# How many days of past forecast errors feed the uncertainty band when planning.
HISTORY_DAYS = 28
# Live mode re-downloads the newest forecast runs and actuals this often.
LIVE_REFRESH_MINUTES = 15

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from environment variables (12-factor style)."""

    elexon_base_url: str
    # "auto": use the committed snapshot if present, else call Elexon live.
    # "snapshot": snapshot only. "live": always call Elexon.
    data_source: str
    snapshot_dir: Path
    # Folder with the built React app; served by FastAPI when running in Docker.
    static_dir: Path | None
    # "replay": the clock moves through January 2026 (snapshot data).
    # "live": the clock is real time and data is a rolling window downloaded from Elexon.
    clock_mode: str = "replay"
    replay_start: datetime = DEFAULT_REPLAY_START
    # Simulated seconds per real second; 0 = paused (move it with POST /api/clock).
    replay_speed: float = 0.0

    @classmethod
    def from_env(cls) -> Settings:
        static = os.environ.get("STATIC_DIR")
        return cls(
            elexon_base_url=os.environ.get(
                "ELEXON_BASE_URL", "https://data.elexon.co.uk/bmrs/api/v1"
            ).rstrip("/"),
            data_source=os.environ.get("DATA_SOURCE", "auto").lower(),
            snapshot_dir=Path(os.environ.get("SNAPSHOT_DIR", BACKEND_DIR / "data")),
            static_dir=Path(static) if static else None,
            clock_mode=_choice("CLOCK_MODE", "replay", CLOCK_MODES),
            replay_start=_moment(os.environ.get("REPLAY_START"), DEFAULT_REPLAY_START),
            replay_speed=float(os.environ.get("REPLAY_SPEED", "0")),
        )


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(name, default).lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, not {value!r}")
    return value


def _moment(value: str | None, default: datetime) -> datetime:
    """ISO date-time from the environment; a time without a zone is UK local time."""
    if not value:
        return default
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UK_TZ)
