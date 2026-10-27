"""Thin client for the two Elexon BMRS datasets we use, plus conversion to tidy DataFrames.

Endpoints (no API key needed):
  FUELHH  /datasets/FUELHH/stream   half-hourly generation outturn by fuel type (actuals)
  WINDFOR /datasets/WINDFOR/stream  wind generation forecasts, one "run" per publishTime
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pandas as pd

log = logging.getLogger(__name__)

# Elexon limits how much one request can return, so long ranges are split into chunks.
CHUNK_DAYS = 7
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ElexonError(RuntimeError):
    """Raised when Elexon cannot be reached or keeps returning errors."""


class ElexonClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        http: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._http = http or httpx.Client(timeout=timeout)

    # ---- public API -------------------------------------------------------------------

    def fetch_wind_actuals(self, first_day: date, last_day: date) -> list[dict[str, Any]]:
        """Half-hourly WIND outturn for settlement dates first_day..last_day (inclusive)."""
        rows: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _date_chunks(first_day, last_day):
            rows += self._get(
                "/datasets/FUELHH/stream",
                {
                    "settlementDateFrom": chunk_start.isoformat(),
                    "settlementDateTo": chunk_end.isoformat(),
                    "fuelType": "WIND",
                },
            )
        return rows

    def fetch_wind_forecasts(self, publish_from: datetime, publish_to: datetime) -> list[dict]:
        """Every WINDFOR forecast run published in [publish_from, publish_to)."""
        rows: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _datetime_chunks(publish_from, publish_to):
            rows += self._get(
                "/datasets/WINDFOR/stream",
                {
                    "publishDateTimeFrom": _iso_utc(chunk_start),
                    "publishDateTimeTo": _iso_utc(chunk_end),
                },
            )
        return rows

    # ---- internals --------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{self._base_url}{path}"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                problem = f"network error: {exc}"
            else:
                if response.status_code == 200:
                    rows = _rows_from(response)
                    if rows is not None:
                        return rows
                    # e.g. an HTML maintenance page served with 200: treat as a failure.
                    problem = f"unexpected response body: {response.text[:80]!r}"
                elif response.status_code not in RETRY_STATUSES:
                    raise ElexonError(
                        f"Elexon {path} returned {response.status_code}: {response.text[:200]}"
                    )
                else:
                    problem = f"HTTP {response.status_code}"
            if attempt < self._max_attempts:
                wait = 2 ** (attempt - 1)
                log.warning("Elexon %s failed (%s); retrying in %ss", path, problem, wait)
                time.sleep(wait)
        raise ElexonError(f"Elexon {path} failed after {self._max_attempts} attempts: {problem}")


def _rows_from(response: httpx.Response) -> list[dict[str, Any]] | None:
    """The list of records in a response, or None if the body is not what we expect.

    Stream endpoints return a bare JSON list; others wrap it as {"data": [...]}.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        payload = payload.get("data")
    return payload if isinstance(payload, list) else None


def _date_chunks(first: date, last: date) -> Iterator[tuple[date, date]]:
    """Inclusive date ranges of at most CHUNK_DAYS days."""
    start = first
    while start <= last:
        end = min(start + timedelta(days=CHUNK_DAYS - 1), last)
        yield start, end
        start = end + timedelta(days=1)


def _datetime_chunks(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Half-open datetime ranges of at most CHUNK_DAYS days."""
    while start < end:
        chunk_end = min(start + timedelta(days=CHUNK_DAYS), end)
        yield start, chunk_end
        start = chunk_end


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- raw JSON -> tidy tables ----------------------------------------------------------


def to_utc(values: pd.Series) -> pd.Series:
    """Parse timestamps to timezone-aware UTC with one fixed precision, so joins line up."""
    return pd.to_datetime(values, utc=True).dt.as_unit("ns")


def empty_frame(columns: list[str]) -> pd.DataFrame:
    """An empty table that still has the right column types."""
    return pd.DataFrame(
        {
            col: pd.Series(dtype="float64" if col.endswith("_mw") else "datetime64[ns, UTC]")
            for col in columns
        }
    )


def _records(rows: list[dict[str, Any]], required: set[str], dataset: str) -> pd.DataFrame:
    """Rows as a table, failing clearly if Elexon has changed its field names."""
    df = pd.DataFrame(rows)
    missing = required - set(df.columns)
    if missing:
        raise ElexonError(f"{dataset} response is missing fields: {sorted(missing)}")
    return df


def actuals_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Columns: start_time, generation_mw. One row per half-hour (latest publication wins)."""
    columns = ["start_time", "generation_mw"]
    if not rows:
        return empty_frame(columns)
    df = _records(rows, {"fuelType", "startTime", "publishTime", "generation"}, "FUELHH")
    df = df[df["fuelType"] == "WIND"]
    df = pd.DataFrame(
        {
            "start_time": to_utc(df["startTime"]),
            "publish_time": to_utc(df["publishTime"]),
            "generation_mw": pd.to_numeric(df["generation"], errors="coerce"),
        }
    ).dropna(subset=["generation_mw"])
    # Outturn can be republished; keep the most recent value for each half-hour.
    df = df.sort_values("publish_time").drop_duplicates("start_time", keep="last")
    return df.sort_values("start_time")[columns].reset_index(drop=True)


def forecasts_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Columns: publish_time, start_time, generation_mw. One row per (run, target time)."""
    columns = ["publish_time", "start_time", "generation_mw"]
    if not rows:
        return empty_frame(columns)
    df = _records(rows, {"startTime", "publishTime", "generation"}, "WINDFOR")
    df = pd.DataFrame(
        {
            "publish_time": to_utc(df["publishTime"]),
            "start_time": to_utc(df["startTime"]),
            "generation_mw": pd.to_numeric(df["generation"], errors="coerce"),
        }
    ).dropna(subset=["generation_mw"])
    # Overlapping request chunks can return the same row twice.
    df = df.drop_duplicates(["publish_time", "start_time"], keep="last")
    return df.sort_values(["publish_time", "start_time"])[columns].reset_index(drop=True)
