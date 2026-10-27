from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from wind_monitor import config, store
from wind_monitor.store import WindData, load_snapshot, load_wind_data, save_snapshot


def _settings(tmp_path, source="auto"):
    return replace(config.Settings.from_env(), data_source=source, snapshot_dir=tmp_path)


def _sample() -> WindData:
    actuals = pd.DataFrame(
        {
            "start_time": pd.to_datetime(["2026-01-02T00:00Z", "2026-01-02T00:30Z"], utc=True),
            "generation_mw": [9000.0, 9100.0],
        }
    )
    forecasts = pd.DataFrame(
        {
            "publish_time": pd.to_datetime(["2026-01-01T08:30Z"], utc=True),
            "start_time": pd.to_datetime(["2026-01-02T00:00Z"], utc=True),
            "generation_mw": [8800.0],
        }
    )
    return WindData(actuals, forecasts, "live", datetime(2026, 10, 1, 12, tzinfo=UTC))


def test_snapshot_round_trip_keeps_values_and_timezones(tmp_path):
    save_snapshot(_sample(), tmp_path)
    loaded = load_snapshot(tmp_path)

    assert loaded is not None
    assert loaded.source == "snapshot"
    assert loaded.retrieved_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    pd.testing.assert_frame_equal(
        loaded.actuals, _sample().actuals.assign(start_time=lambda d: d.start_time.dt.as_unit("ns"))
    )
    assert str(loaded.forecasts["publish_time"].dtype) == "datetime64[ns, UTC]"


def test_missing_snapshot_returns_none(tmp_path):
    assert load_snapshot(tmp_path) is None


def test_auto_mode_prefers_snapshot(tmp_path, monkeypatch):
    save_snapshot(_sample(), tmp_path)
    monkeypatch.setattr(store, "fetch_live", lambda s: pytest.fail("should not call Elexon"))
    assert load_wind_data(_settings(tmp_path)).source == "snapshot"


def test_auto_mode_falls_back_to_live(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "fetch_live", lambda s: _sample())
    assert load_wind_data(_settings(tmp_path)).source == "live"


def test_snapshot_mode_fails_loudly_without_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_wind_data(_settings(tmp_path, source="snapshot"))


def test_live_mode_ignores_snapshot(tmp_path, monkeypatch):
    save_snapshot(_sample(), tmp_path)
    monkeypatch.setattr(store, "fetch_live", lambda s: _sample())
    assert load_wind_data(_settings(tmp_path, source="live")).source == "live"
