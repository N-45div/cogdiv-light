"""How clean GB electricity is, half-hour by half-hour, nationally and in each of 14 regions.

NESO's Carbon Intensity API (api.carbonintensity.org.uk, built with the University of Oxford
and WWF; free, no key) forecasts the grams of CO2 per kWh of every half-hour for the next 48
hours: for Great Britain, and for each of the 14 distribution network regions. Wind is what
makes it swing. On a windy night North Scotland runs on wind at about 0 g/kWh while South Wales
and the South West burn gas at 350 or more. A job that can move in time (an EV charge) or in
place (a batch of AI compute) can follow it.

Region ids 1-14 are the distribution regions, 15-17 (England, Scotland, Wales) are aggregates
and 18 is GB. The forecast is re-issued every half-hour, so a download is kept for 30 minutes.
Past dates work too (the API keeps its forecasts), so replaying January 2026 has carbon data.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import httpx
import pandas as pd

log = logging.getLogger(__name__)

API_URL = "https://api.carbonintensity.org.uk"
# The 14 distribution network regions, by the API's region id.
REGIONS: dict[int, str] = {
    1: "North Scotland",
    2: "South Scotland",
    3: "North West England",
    4: "North East England",
    5: "Yorkshire",
    6: "North Wales & Merseyside",
    7: "South Wales",
    8: "West Midlands",
    9: "East Midlands",
    10: "East England",
    11: "South West England",
    12: "South England",
    13: "London",
    14: "South East England",
}
GB_REGION = 18
HALF_HOUR = pd.Timedelta(minutes=30)
RETRY = pd.Timedelta(minutes=2)


class CarbonUnavailableError(RuntimeError):
    """The carbon forecast could not be downloaded and nothing usable is held."""


def region_id(name: str) -> int:
    """'north scotland', 'London' or '13' -> the region id."""
    text = name.strip().lower()
    if text.isdigit() and int(text) in REGIONS:
        return int(text)
    for rid, region in REGIONS.items():
        if region.lower() == text:
            return rid
    for rid, region in REGIONS.items():  # "Scotland north", "SW England": loose match
        if all(word in region.lower() for word in text.replace("&", " ").split()):
            return rid
    raise ValueError(f"Unknown region {name!r}: use one of {', '.join(REGIONS.values())}")


@dataclass(frozen=True)
class CarbonForecast:
    # One row per region per half-hour: start_time (UTC), region_id, region, intensity_g,
    # index ("very low" .. "very high"), and the forecast generation mix as <fuel>_pct columns.
    regions: pd.DataFrame
    national: pd.DataFrame  # start_time, intensity_g, index (GB as a whole)
    fetched_at: pd.Timestamp
    starts: pd.Timestamp  # the clock's half-hour when it was downloaded

    @property
    def ends(self) -> pd.Timestamp:
        last = self.national["start_time"].max() if len(self.national) else self.starts
        return last + HALF_HOUR

    def region(self, rid: int) -> pd.DataFrame:
        """start_time, intensity_g for one region (GB_REGION for the national figure)."""
        if rid == GB_REGION:
            return self.national[["start_time", "intensity_g"]]
        rows = self.regions[self.regions["region_id"] == rid]
        return rows[["start_time", "intensity_g"]].reset_index(drop=True)

    def at(self, moment: pd.Timestamp) -> pd.DataFrame:
        """Every region's row for the half-hour containing `moment`."""
        slot = moment.floor("30min")
        return self.regions[self.regions["start_time"] == slot].reset_index(drop=True)


def forecast_frames(periods: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The API's regional periods -> (regions, national) frames."""
    regional, national = [], []
    for period in periods:
        start = period["from"]
        for r in period.get("regions", []):
            intensity = r.get("intensity") or {}
            row = {
                "start_time": start,
                "region_id": int(r["regionid"]),
                "intensity_g": intensity.get("forecast"),
                "index": intensity.get("index"),
            }
            if row["region_id"] == GB_REGION:
                national.append(row)
            elif row["region_id"] in REGIONS:
                row["region"] = REGIONS[row["region_id"]]
                for mix in r.get("generationmix") or []:
                    row[f"{mix['fuel']}_pct"] = mix["perc"]
                regional.append(row)
    regions = pd.DataFrame(regional)
    nat = pd.DataFrame(national, columns=["start_time", "region_id", "intensity_g", "index"])
    for frame in (regions, nat):
        if len(frame):
            frame["start_time"] = pd.to_datetime(frame["start_time"], utc=True).dt.as_unit("ns")
            frame["intensity_g"] = frame["intensity_g"].astype(float)
    nat = nat.drop(columns="region_id")
    if regions.empty:
        regions = pd.DataFrame(columns=["start_time", "region_id", "region", "intensity_g"])
    sort = ["start_time", "region_id"]
    return regions.sort_values(sort).reset_index(drop=True), nat.sort_values("start_time")


class CarbonClient:
    def __init__(self, base_url: str = API_URL, http: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=20.0)

    def fetch_regional(self, start: pd.Timestamp) -> list[dict]:
        """The 48 hours of regional forecasts from the half-hour containing `start`."""
        url = f"{self._base_url}/regional/intensity/{start:%Y-%m-%dT%H:%MZ}/fw48h"
        try:
            response = self._http.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()["data"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise CarbonUnavailableError(f"Carbon Intensity API: {exc}") from exc


class CarbonFeed:
    """The 48-hour carbon forecast from the clock's current half-hour. Live, that means a new
    download every half-hour, when NESO re-issues it; in replay, one per moment visited. If a
    download fails, the forecast already held is used while it still reaches 42 hours ahead,
    and the next try waits two minutes."""

    def __init__(self, client, retry: pd.Timedelta = RETRY):
        self._client = client
        self._retry = retry
        self._held: CarbonForecast | None = None
        self._retry_at: pd.Timestamp | None = None
        self._lock = threading.Lock()

    def _fresh(self, now: pd.Timestamp) -> bool:
        held = self._held
        return held is not None and held.starts <= now < held.starts + HALF_HOUR

    def _usable(self, now: pd.Timestamp) -> bool:
        held = self._held
        return held is not None and held.starts <= now < held.ends - pd.Timedelta(hours=6)

    def get(self, now: pd.Timestamp) -> CarbonForecast:
        with self._lock:
            if self._fresh(now):
                return self._held
            real_now = pd.Timestamp.now(tz="UTC")
            if self._retry_at is not None and real_now < self._retry_at:
                if self._usable(now):
                    return self._held
                raise CarbonUnavailableError("The Carbon Intensity API isn't answering just now")
            try:
                regions, national = forecast_frames(self._client.fetch_regional(now))
            except CarbonUnavailableError as exc:
                self._retry_at = real_now + self._retry
                if self._usable(now):
                    log.warning("Carbon forecast refresh failed, using the one held: %s", exc)
                    return self._held
                raise
            if national.empty:
                raise CarbonUnavailableError("The Carbon Intensity API returned no forecast")
            # The API may start a half-hour early (at 09:00 it begins with 08:30-09:00), so the
            # forecast is filed under the clock's half-hour, not its first row.
            self._held = CarbonForecast(regions, national, real_now, now.floor("30min"))
            self._retry_at = None
            return self._held


_feed: CarbonFeed | None = None
_feed_lock = threading.Lock()


def get_carbon_feed() -> CarbonFeed:
    global _feed
    if _feed is None:
        with _feed_lock:
            if _feed is None:
                _feed = CarbonFeed(CarbonClient())
    return _feed
