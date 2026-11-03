"""Shared test setup: no test may download the real carbon forecast."""

import pytest
from wind_fixtures import carbon_periods

from wind_monitor import carbon


class SyntheticCarbonClient:
    """Serves the synthetic regional forecast (see wind_fixtures.carbon_g) for any start."""

    def __init__(self) -> None:
        self.calls = []

    def fetch_regional(self, start):
        self.calls.append(start)
        return carbon_periods(start)


@pytest.fixture(autouse=True)
def synthetic_carbon_feed(monkeypatch):
    feed = carbon.CarbonFeed(SyntheticCarbonClient())
    monkeypatch.setattr(carbon, "_feed", feed)
    return feed
