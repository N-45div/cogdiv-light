"""Live mode rolling window: one full download, then small top-ups; old data if a top-up fails."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from wind_monitor.elexon import ElexonError
from wind_monitor.store import DataUnavailableError, LiveWindow

NOW = pd.Timestamp("2026-10-28T12:00Z")


def _iso(moment: pd.Timestamp) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeElexon:
    """Elexon as seen at `now`: a run every 6 hours, an actual every half-hour (published an
    hour after it starts). Records every request."""

    def __init__(self, now: pd.Timestamp) -> None:
        self.now = now
        self.calls: list[tuple] = []
        self.down = False
        self.revisions: dict[pd.Timestamp, float] = {}

    def fetch_wind_actuals(self, first_day: date, last_day: date) -> list[dict]:
        self.calls.append(("actuals", first_day, last_day))
        if self.down:
            raise ElexonError("Elexon is down")
        start = pd.Timestamp(first_day, tz="UTC")
        end = pd.Timestamp(last_day + timedelta(days=1), tz="UTC")
        rows = []
        for moment in pd.date_range(start, end, freq="30min", inclusive="left"):
            published = moment + pd.Timedelta(hours=1)
            if published <= self.now:
                mw = self.revisions.get(moment, 10_000.0)
                rows.append(
                    {
                        "fuelType": "WIND",
                        "startTime": _iso(moment),
                        "publishTime": _iso(published),
                        "generation": mw,
                    }
                )
        return rows

    def fetch_wind_forecasts(self, publish_from: datetime, publish_to: datetime) -> list[dict]:
        self.calls.append(("forecasts", publish_from, publish_to))
        if self.down:
            raise ElexonError("Elexon is down")
        first = pd.Timestamp(publish_from).ceil("6h")
        rows = []
        for run in pd.date_range(first, pd.Timestamp(publish_to), freq="6h"):
            if run <= self.now:
                for h in range(1, 4):
                    target = run + pd.Timedelta(hours=h)
                    rows.append(
                        {"startTime": _iso(target), "publishTime": _iso(run), "generation": 11_000}
                    )
        return rows


def _window(fake: FakeElexon) -> LiveWindow:
    return LiveWindow(fake, days=28, refresh=timedelta(minutes=15), retry=timedelta(minutes=2))


def test_first_call_downloads_the_whole_window():
    fake = FakeElexon(NOW)
    data = _window(fake).get(NOW)
    keep_from = NOW - pd.Timedelta(days=30)
    assert fake.calls == [
        ("actuals", keep_from.date(), NOW.date()),
        ("forecasts", keep_from.to_pydatetime(), NOW.to_pydatetime()),
    ]
    assert data.source == "live"
    assert data.actuals["start_time"].min() >= keep_from
    assert data.actuals["start_time"].max() == NOW - pd.Timedelta(hours=1)
    assert data.forecasts["publish_time"].max() == NOW


def test_no_download_until_the_refresh_interval_has_passed():
    fake = FakeElexon(NOW)
    window = _window(fake)
    first = window.get(NOW)
    assert window.get(NOW + pd.Timedelta(minutes=14)) is first
    assert len(fake.calls) == 2


def test_top_up_fetches_only_the_last_day_and_merges_it():
    fake = FakeElexon(NOW)
    window = _window(fake)
    before = window.get(NOW)
    later = NOW + pd.Timedelta(hours=6)
    fake.now = later
    # Elexon revises an actual that was already downloaded.
    revised = NOW - pd.Timedelta(hours=3)
    fake.revisions[revised] = 9_500.0

    after = window.get(later)

    assert fake.calls[-2:] == [
        ("actuals", (later - pd.Timedelta(days=1)).date(), later.date()),
        ("forecasts", (later - pd.Timedelta(days=1)).to_pydatetime(), later.to_pydatetime()),
    ]
    assert after.forecasts["publish_time"].max() == later  # the new run arrived
    assert after.actuals["start_time"].max() == later - pd.Timedelta(hours=1)
    assert after.actuals.set_index("start_time").loc[revised, "generation_mw"] == 9_500.0
    # History downloaded earlier is kept (except what fell out of the window).
    assert after.actuals["start_time"].min() >= later - pd.Timedelta(days=30)
    assert after.actuals["start_time"].is_unique
    # 6 h later: 12 new half-hours in, the 12 oldest trimmed.
    assert len(after.actuals) == len(before.actuals)
    assert not after.forecasts.duplicated(["publish_time", "start_time"]).any()


def test_failed_top_up_serves_the_data_already_held_and_retries_later():
    fake = FakeElexon(NOW)
    window = _window(fake)
    first = window.get(NOW)
    fake.down = True

    assert window.get(NOW + pd.Timedelta(minutes=20)) is first
    calls = len(fake.calls)
    assert window.get(NOW + pd.Timedelta(minutes=21)) is first
    assert len(fake.calls) == calls  # waits `retry` before trying again

    fake.down = False
    fake.now = NOW + pd.Timedelta(minutes=23)
    assert window.get(NOW + pd.Timedelta(minutes=23)) is not first


def test_failed_first_download_is_an_error_and_is_retried():
    fake = FakeElexon(NOW)
    fake.down = True
    window = _window(fake)
    with pytest.raises(DataUnavailableError):
        window.get(NOW)
    fake.down = False
    assert window.get(NOW).source == "live"
