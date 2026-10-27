from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from wind_monitor import elexon
from wind_monitor.elexon import ElexonClient, ElexonError, actuals_frame, forecasts_frame

BASE = "https://elexon.test/bmrs/api/v1"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(elexon.time, "sleep", lambda _: None)


@respx.mock
def test_actuals_request_is_split_into_week_chunks_with_wind_filter():
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        return_value=httpx.Response(200, json=[])
    )
    ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 10))

    params = [dict(call.request.url.params) for call in route.calls]
    assert params == [
        {"settlementDateFrom": "2026-01-01", "settlementDateTo": "2026-01-07", "fuelType": "WIND"},
        {"settlementDateFrom": "2026-01-08", "settlementDateTo": "2026-01-10", "fuelType": "WIND"},
    ]


@respx.mock
def test_forecast_request_uses_publish_time_window_in_utc():
    route = respx.get(f"{BASE}/datasets/WINDFOR/stream").mock(
        return_value=httpx.Response(200, json=[])
    )
    ElexonClient(BASE).fetch_wind_forecasts(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 9, tzinfo=UTC)
    )

    windows = [
        (
            call.request.url.params["publishDateTimeFrom"],
            call.request.url.params["publishDateTimeTo"],
        )
        for call in route.calls
    ]
    assert windows == [
        ("2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z"),
        ("2026-01-08T00:00:00Z", "2026-01-09T00:00:00Z"),
    ]


@respx.mock
def test_retries_on_rate_limit_then_succeeds():
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=[{"x": 1}])]
    )
    rows = ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1))
    assert rows == [{"x": 1}]
    assert route.call_count == 2


@respx.mock
def test_bad_request_is_not_retried():
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        return_value=httpx.Response(400, text="bad params")
    )
    with pytest.raises(ElexonError, match="400"):
        ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1))
    assert route.call_count == 1


@respx.mock
def test_gives_up_after_max_attempts():
    respx.get(f"{BASE}/datasets/FUELHH/stream").mock(return_value=httpx.Response(503))
    with pytest.raises(ElexonError, match="after 3 attempts"):
        ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1))


@respx.mock
def test_accepts_wrapped_data_payload():
    respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        return_value=httpx.Response(200, json={"data": [{"x": 1}]})
    )
    assert ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1)) == [{"x": 1}]


def test_actuals_frame_keeps_wind_only_and_latest_publication():
    rows = [
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T00:30:00Z",
         "fuelType": "WIND", "generation": 9000},
        # republished later with a corrected value -> this one wins
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:30:00Z",
         "fuelType": "WIND", "generation": 9100},
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T00:30:00Z",
         "fuelType": "CCGT", "generation": 5000},
    ]  # fmt: skip
    df = actuals_frame(rows)
    assert list(df.columns) == ["start_time", "generation_mw"]
    assert df["generation_mw"].tolist() == [9100]
    assert str(df["start_time"].dtype) == "datetime64[ns, UTC]"


def test_forecasts_frame_drops_duplicate_rows():
    row = {"publishTime": "2026-01-01T08:30:00Z", "startTime": "2026-01-02T00:00:00Z",
           "generation": 8000}  # fmt: skip
    df = forecasts_frame([row, dict(row)])
    assert len(df) == 1
    assert list(df.columns) == ["publish_time", "start_time", "generation_mw"]


def test_empty_responses_give_typed_empty_tables():
    assert str(actuals_frame([])["start_time"].dtype) == "datetime64[ns, UTC]"
    assert str(forecasts_frame([])["generation_mw"].dtype) == "float64"


@respx.mock
def test_html_page_with_status_200_is_retried_then_reported():
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        return_value=httpx.Response(200, text="<html>Down for maintenance</html>")
    )
    with pytest.raises(ElexonError, match="unexpected response body"):
        ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1))
    assert route.call_count == 3


@respx.mock
def test_json_that_is_not_a_list_of_records_is_rejected():
    respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        return_value=httpx.Response(200, json={"error": "something"})
    )
    with pytest.raises(ElexonError):
        ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1))


@respx.mock
def test_network_errors_are_retried():
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[])]
    )
    assert ElexonClient(BASE).fetch_wind_actuals(date(2026, 1, 1), date(2026, 1, 1)) == []
    assert route.call_count == 2


def test_renamed_fields_raise_a_clear_error():
    with pytest.raises(ElexonError, match="missing fields"):
        forecasts_frame([{"publishTime": "2026-01-01T08:30:00Z", "start": "x", "generation": 1}])


def test_rows_without_a_usable_generation_value_are_dropped():
    rows = [
        {"publishTime": "2026-01-01T08:30:00Z", "startTime": "2026-01-02T00:00:00Z",
         "generation": None},
        {"publishTime": "2026-01-01T08:30:00Z", "startTime": "2026-01-02T01:00:00Z",
         "generation": "n/a"},
        {"publishTime": "2026-01-01T08:30:00Z", "startTime": "2026-01-02T02:00:00Z",
         "generation": 7000},
    ]  # fmt: skip
    assert forecasts_frame(rows)["generation_mw"].tolist() == [7000]
