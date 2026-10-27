"""Where the app gets its data: a committed snapshot file, or Elexon live.

January 2026 is in the past, so the data no longer changes. We therefore save it once as a
snapshot (CSV files in backend/data) and serve from that: fast cold starts, no dependency on
Elexon being up during a demo, and reproducible numbers. `scripts/fetch_snapshot.py`
re-downloads it, and DATA_SOURCE=live skips the snapshot entirely.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import config
from .elexon import ElexonClient, ElexonError, actuals_frame, forecasts_frame, to_utc

log = logging.getLogger(__name__)

ACTUALS_FILE = "wind_actuals.csv"
FORECASTS_FILE = "wind_forecasts.csv"
META_FILE = "snapshot_meta.json"
CSV_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class WindData:
    actuals: pd.DataFrame  # start_time, generation_mw (half-hourly)
    forecasts: pd.DataFrame  # publish_time, start_time, generation_mw (one row per run x target)
    source: str  # "snapshot" or "live"
    retrieved_at: datetime  # when the data was downloaded from Elexon
    # Results computed from this data (e.g. errors per cutoff), so repeat requests are instant.
    cache: dict = field(default_factory=dict, compare=False, repr=False)


def fetch_live(settings: config.Settings, client: ElexonClient | None = None) -> WindData:
    client = client or ElexonClient(settings.elexon_base_url)
    log.info("Downloading wind actuals and forecasts from Elexon")
    actuals = actuals_frame(client.fetch_wind_actuals(config.ACTUALS_FROM, config.ACTUALS_TO))
    forecasts = forecasts_frame(
        client.fetch_wind_forecasts(config.FORECAST_PUBLISH_FROM, config.FORECAST_PUBLISH_TO)
    )
    return WindData(actuals, forecasts, source="live", retrieved_at=datetime.now(UTC))


def save_snapshot(data: WindData, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data.actuals.to_csv(folder / ACTUALS_FILE, index=False, date_format=CSV_TIME_FORMAT)
    data.forecasts.to_csv(folder / FORECASTS_FILE, index=False, date_format=CSV_TIME_FORMAT)
    meta = {
        "retrieved_at": data.retrieved_at.strftime(CSV_TIME_FORMAT),
        "actuals_rows": len(data.actuals),
        "forecast_rows": len(data.forecasts),
        "source": "https://data.elexon.co.uk/bmrs/api/v1 (FUELHH fuelType=WIND, WINDFOR)",
    }
    (folder / META_FILE).write_text(json.dumps(meta, indent=2) + "\n")


def load_snapshot(folder: Path) -> WindData | None:
    files = [folder / ACTUALS_FILE, folder / FORECASTS_FILE, folder / META_FILE]
    if not all(f.exists() for f in files):
        return None
    actuals = pd.read_csv(files[0])
    actuals["start_time"] = to_utc(actuals["start_time"])
    forecasts = pd.read_csv(files[1])
    forecasts["publish_time"] = to_utc(forecasts["publish_time"])
    forecasts["start_time"] = to_utc(forecasts["start_time"])
    meta = json.loads(files[2].read_text())
    retrieved_at = datetime.strptime(meta["retrieved_at"], CSV_TIME_FORMAT).replace(tzinfo=UTC)
    return WindData(actuals, forecasts, source="snapshot", retrieved_at=retrieved_at)


def load_wind_data(settings: config.Settings) -> WindData:
    if settings.data_source != "live":
        snapshot = load_snapshot(settings.snapshot_dir)
        if snapshot is not None:
            return snapshot
        if settings.data_source == "snapshot":
            raise FileNotFoundError(f"No snapshot found in {settings.snapshot_dir}")
    return fetch_live(settings)


class DataUnavailableError(RuntimeError):
    """The data could not be loaded (Elexon down, or no snapshot in snapshot-only mode)."""


# One copy per process. A lock stops two first requests from downloading twice.
# A failed load is not cached, so the next request tries again.
_cache: WindData | None = None
_lock = threading.Lock()


def get_wind_data() -> WindData:
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                try:
                    _cache = load_wind_data(config.Settings.from_env())
                except (ElexonError, FileNotFoundError) as exc:
                    raise DataUnavailableError(str(exc)) from exc
    return _cache
