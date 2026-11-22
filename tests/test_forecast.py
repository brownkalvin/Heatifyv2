from datetime import date

import pytest

from warmly.model import forecast
from warmly.model.types import ForecastPoint
from helpers import at


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


OPEN_METEO_REPLY = {
    "hourly": {
        "time": ["2026-11-22T00:00", "2026-11-22T01:00", "2026-11-22T02:00"],
        "temperature_2m": [4.0, 3.5, None],
    }
}


def test_fallback():
    """Network fails -> hard-coded night. Then live once -> cache. Never raises."""
    points, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "fallback"
    assert min(p.temp_c for p in points) == -2.0

    # A successful fetch fills the cache...
    forecast.requests.get = lambda *a, **k: FakeResponse(OPEN_METEO_REPLY)
    points, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "live"
    assert [p.temp_c for p in points] == [4.0, 3.5]  # None hour dropped

    # ...so when the network fails again, the cache is used.
    def refuse(*a, **k):
        raise ConnectionError("offline")
    forecast.requests.get = refuse
    points, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "cache"
    assert points[0].timestamp == at(22, 0)


def test_corrupt_cache_uses_fallback():
    forecast.CACHE_PATH.write_text("not json", encoding="utf-8")
    points, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "fallback"


def test_historical_fallback():
    points = forecast.get_historical(51.75, -1.26, date(2025, 1, 8), date(2025, 1, 9))
    assert points[0].timestamp.date() == date(2025, 1, 8)


def test_postcode_falls_back_to_oxford():
    assert forecast.postcode_to_latlon("OX4 1AA") == forecast.OXFORD_LATLON


def test_interpolation():
    points = [ForecastPoint(at(22, 1), 2.0), ForecastPoint(at(22, 2), 4.0)]
    assert forecast.temp_at(points, at(22, 1, 30)) == pytest.approx(3.0)
    # Clamped outside the range
    assert forecast.temp_at(points, at(22, 0)) == 2.0
    assert forecast.temp_at(points, at(22, 5)) == 4.0


def test_snow_flag():
    reply = {"hourly": {
        "time": ["2026-11-22T22:00", "2026-11-22T23:00", "2026-11-23T00:00"],
        "temperature_2m": [1.0, -0.5, -1.0],
        "snowfall": [0.0, 0.2, None],
    }}
    forecast.requests.get = lambda *a, **k: FakeResponse(reply)
    points, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "live"
    assert [p.snow for p in points] == [False, True, False]
    assert forecast.snow_tonight(points, at(22, 21))
    assert forecast.freezing_tonight(points, at(22, 21))
    assert not forecast.snow_tonight(points, at(23, 0))   # the snowy hour has passed

    # The snow flag survives the cache
    def refuse(*a, **k):
        raise ConnectionError("offline")
    forecast.requests.get = refuse
    cached, source = forecast.get_hourly_forecast_with_source(51.75, -1.26)
    assert source == "cache" and [p.snow for p in cached] == [False, True, False]


def test_coldest_night():
    from warmly.model.types import ForecastPoint as FP
    points = [FP(at(22, 23), 1.0), FP(at(23, 3), -1.0),     # night of the 22nd: low -1
              FP(at(23, 12), -9.0),                        # daytime: ignored
              FP(at(23, 22), 0.0), FP(at(24, 5), -3.0)]    # night of the 23rd: low -3
    assert forecast.coldest_night(points) == date(2026, 11, 23)
    assert forecast.coldest_night([]) is None
