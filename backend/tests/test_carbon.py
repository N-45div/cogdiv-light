"""The carbon intensity feed: parsing NESO's regional forecast, and when it downloads again."""

import pandas as pd
import pytest
from wind_fixtures import NOW, carbon_periods, synthetic_carbon

from wind_monitor.carbon import (
    GB_REGION,
    CarbonFeed,
    CarbonUnavailableError,
    forecast_frames,
    region_id,
)


def test_regional_periods_become_tidy_frames():
    regions, national = forecast_frames(carbon_periods())
    assert len(national) == 97 and national["start_time"].iloc[0] == pd.Timestamp(
        "2026-01-08T17:30Z"
    )
    assert set(regions["region_id"]) == set(range(1, 15))  # aggregates 15-17 are left out
    assert len(regions) == 97 * 14
    row = regions[(regions["region_id"] == 1) & (regions["start_time"] == NOW)].iloc[0]
    assert row["region"] == "North Scotland" and row["intensity_g"] == 10
    assert row["wind_pct"] == 90 and row["gas_pct"] == 5


def test_a_forecast_by_region_and_by_moment():
    forecast = synthetic_carbon()
    night = forecast.region(1)
    assert night.set_index("start_time")["intensity_g"][pd.Timestamp("2026-01-09T02:00Z")] == 0
    gb = forecast.region(GB_REGION).set_index("start_time")["intensity_g"]
    assert gb[pd.Timestamp("2026-01-09T04:30Z")] == 60
    now = forecast.at(NOW + pd.Timedelta(minutes=10))
    assert len(now) == 14 and set(now["start_time"]) == {NOW}
    assert forecast.ends == pd.Timestamp("2026-01-10T18:00Z")


@pytest.mark.parametrize(
    ("name", "rid"),
    [("North Scotland", 1), ("london", 13), ("13", 13), ("scotland north", 1), ("Yorkshire", 5)],
)
def test_region_names(name, rid):
    assert region_id(name) == rid


def test_unknown_region():
    with pytest.raises(ValueError, match="Unknown region"):
        region_id("Mars")


class FakeClient:
    def __init__(self):
        self.calls, self.fail = [], False

    def fetch_regional(self, start):
        self.calls.append(start)
        if self.fail:
            raise CarbonUnavailableError("down")
        return carbon_periods(start)


def test_the_feed_downloads_once_per_half_hour():
    client = FakeClient()
    feed = CarbonFeed(client)
    first = feed.get(NOW)
    assert feed.get(NOW + pd.Timedelta(minutes=29)) is first
    assert len(client.calls) == 1
    later = feed.get(NOW + pd.Timedelta(minutes=30))
    assert later is not first and len(client.calls) == 2
    feed.get(NOW - pd.Timedelta(days=3))  # a replay jump back downloads too
    assert len(client.calls) == 3


def test_a_failed_download_keeps_the_forecast_held():
    client = FakeClient()
    feed = CarbonFeed(client)
    held = feed.get(NOW)
    client.fail = True
    assert feed.get(NOW + pd.Timedelta(hours=1)) is held
    assert feed.get(NOW + pd.Timedelta(hours=2)) is held  # waits before trying again
    assert len(client.calls) == 2
    with pytest.raises(CarbonUnavailableError):
        feed.get(NOW + pd.Timedelta(hours=45))  # too little of the old forecast is left


def test_no_forecast_at_all_is_an_error():
    client = FakeClient()
    client.fail = True
    with pytest.raises(CarbonUnavailableError):
        CarbonFeed(client).get(NOW)
