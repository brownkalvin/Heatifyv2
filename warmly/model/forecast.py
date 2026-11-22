"""Outdoor temperature: live forecast, history, cache and fallback.

The demo must never crash on stage because of the network, so every fetch
falls back: live API -> data/forecast_cache.json -> data/fallback_forecast.json.
"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import requests

from .homes import LONDON
from .types import ForecastPoint

OXFORD_LATLON = (51.752, -1.258)
REQUEST_TIMEOUT_S = 10

POSTCODE_URL = "https://api.postcodes.io/postcodes/{postcode}"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_PATH = DATA_DIR / "forecast_cache.json"
COLD_NIGHT_PATH = DATA_DIR / "cold_night.json"
COLD_SEARCH = (date(2025, 12, 1), date(2026, 2, 28))   # last winter
FALLBACK_PATH = DATA_DIR / "fallback_forecast.json"


# --------------------------------------------------------------------------- #
# Postcode
# --------------------------------------------------------------------------- #
def postcode_to_latlon(postcode: str) -> tuple[float, float]:
    """Look up a UK postcode. Returns Oxford if the lookup fails."""
    try:
        url = POSTCODE_URL.format(postcode=postcode.replace(" ", ""))
        r = requests.get(url, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        result = r.json()["result"]
        return float(result["latitude"]), float(result["longitude"])
    except Exception:
        return OXFORD_LATLON


# --------------------------------------------------------------------------- #
# Forecast and history
# --------------------------------------------------------------------------- #
def get_hourly_forecast(lat: float, lon: float, days: int = 7,
                        past_days: int = 0) -> list[ForecastPoint]:
    """Hourly outdoor forecast starting today (plus `past_days` before it).
    Never raises."""
    points, _source = get_hourly_forecast_with_source(lat, lon, days, past_days)
    return points


def get_hourly_forecast_with_source(lat: float, lon: float,
                                    days: int = 7, past_days: int = 0
                                    ) -> tuple[list[ForecastPoint], str]:
    """Like get_hourly_forecast, but also says where the data came from:
    "live", "cache" or "fallback"."""
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,snowfall", "timezone": "Europe/London",
        "forecast_days": days, "past_days": past_days,
    }
    return _fetch_with_fallback(FORECAST_URL, params, "forecast", date.today())


def get_historical(lat: float, lon: float, start: date, end: date) -> list[ForecastPoint]:
    """Hourly past temperatures, e.g. to replay a real cold night. Never raises."""
    points, _source = get_historical_with_source(lat, lon, start, end)
    return points


def get_historical_with_source(lat: float, lon: float, start: date,
                               end: date) -> tuple[list[ForecastPoint], str]:
    """Like get_historical, but also says "live", "cache" or "fallback"."""
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": "temperature_2m", "timezone": "Europe/London",
    }
    cache_key = f"historical:{start.isoformat()}:{end.isoformat()}"
    return _fetch_with_fallback(ARCHIVE_URL, params, cache_key, start)


def _fetch_with_fallback(url: str, params: dict, cache_key: str,
                         fallback_night: date) -> tuple[list[ForecastPoint], str]:
    """Try the API, then the cache, then the hard-coded cold night."""
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        points = _parse_open_meteo(r.json())
        if points:
            _save_to_cache(cache_key, points)
            return points, "live"
    except Exception:
        pass  # any network or parsing problem: use the cache instead

    cached = _load_from_cache(cache_key)
    if cached:
        return cached, "cache"
    return load_fallback(fallback_night), "fallback"


def _parse_open_meteo(data: dict) -> list[ForecastPoint]:
    """Turn an Open-Meteo hourly response into ForecastPoints."""
    times = data["hourly"]["time"]
    temps = data["hourly"]["temperature_2m"]
    snowfall = data["hourly"].get("snowfall") or [None] * len(times)  # history has none
    points = []
    for t, temp, snow_cm in zip(times, temps, snowfall):
        if temp is None:
            continue  # the API leaves gaps for hours it doesn't have yet
        when = datetime.fromisoformat(t).replace(tzinfo=LONDON)
        points.append(ForecastPoint(when, float(temp), bool(snow_cm and snow_cm > 0)))
    return sorted(points, key=lambda p: p.timestamp)


# --------------------------------------------------------------------------- #
# Cache and fallback
# --------------------------------------------------------------------------- #
def _save_to_cache(key: str, points: list[ForecastPoint]) -> None:
    """Store points under `key` in the cache file. Errors are ignored."""
    try:
        cache = _read_cache_file()
        cache[key] = [[p.timestamp.isoformat(), p.temp_c, p.snow] for p in points]
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def _load_from_cache(key: str) -> list[ForecastPoint] | None:
    """Points stored under `key`, or None if there aren't any."""
    try:
        rows = _read_cache_file().get(key)
        if not rows:
            return None
        # Older cache rows are [time, temp] with no snow flag
        return [ForecastPoint(datetime.fromisoformat(row[0]).astimezone(LONDON), float(row[1]),
                              bool(row[2]) if len(row) > 2 else False)
                for row in rows]
    except Exception:
        return None


def _read_cache_file() -> dict:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def load_fallback(night_of: date) -> list[ForecastPoint]:
    """The hard-coded cold night, starting at 16:00 on `night_of`."""
    data = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
    start = datetime.combine(night_of, time.fromisoformat(data["start_time"]), tzinfo=LONDON)
    return [ForecastPoint(start + timedelta(hours=i), float(temp))
            for i, temp in enumerate(data["hourly_temps_c"])]


# --------------------------------------------------------------------------- #
# Interpolation
# --------------------------------------------------------------------------- #
def temp_at(forecast: list[ForecastPoint], when: datetime) -> float:
    """Outdoor temperature at `when`, interpolated linearly between hourly
    points. Before the first point or after the last, uses that point."""
    if not forecast:
        raise ValueError("forecast is empty")
    if when <= forecast[0].timestamp:
        return forecast[0].temp_c
    if when >= forecast[-1].timestamp:
        return forecast[-1].temp_c

    for before, after in zip(forecast, forecast[1:]):
        if before.timestamp <= when <= after.timestamp:
            span_s = (after.timestamp - before.timestamp).total_seconds()
            fraction = (when - before.timestamp).total_seconds() / span_s
            return before.temp_c + fraction * (after.temp_c - before.temp_c)
    return forecast[-1].temp_c  # not reached if the forecast is sorted


# --------------------------------------------------------------------------- #
# Risk flags for tonight (snow and frost don't change the physics)
# --------------------------------------------------------------------------- #
def tonight(forecast: list[ForecastPoint], now: datetime) -> list[ForecastPoint]:
    """Forecast points from `now` until 07:00 the next morning."""
    end = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    return [p for p in forecast if now <= p.timestamp <= end]


def snow_tonight(forecast: list[ForecastPoint], now: datetime) -> bool:
    """Is any snow forecast between now and 07:00?"""
    return any(p.snow for p in tonight(forecast, now))


def freezing_tonight(forecast: list[ForecastPoint], now: datetime) -> bool:
    """Does it go below 0°C outside between now and 07:00?"""
    return any(p.temp_c < 0 for p in tonight(forecast, now))


def coldest_night(points: list[ForecastPoint]) -> date | None:
    """The night (22:00-07:00, named by its evening) with the lowest
    temperature in `points`, e.g. to pick a cold night to replay."""
    lows: dict[date, float] = {}
    for p in points:
        clock = p.timestamp.time()
        if clock >= time(22):
            night = p.timestamp.date()
        elif clock < time(7):
            night = p.timestamp.date() - timedelta(days=1)
        else:
            continue
        lows[night] = min(lows.get(night, p.temp_c), p.temp_c)
    return min(lows, key=lows.get) if lows else None


def get_cold_night(lat: float = OXFORD_LATLON[0], lon: float = OXFORD_LATLON[1]
                   ) -> tuple[list[ForecastPoint], str, date]:
    """Last winter's coldest night, for when tonight is too mild to demo.

    Returns (points, source, night_of). source is "cold_night" (from the
    archive, or the copy saved in data/cold_night.json) or "fallback" if
    neither is available. One archive call; never raises.
    """
    try:
        data = json.loads(COLD_NIGHT_PATH.read_text(encoding="utf-8"))
        points = [ForecastPoint(datetime.fromisoformat(t).astimezone(LONDON), float(temp), bool(snow))
                  for t, temp, snow in data["points"]]
        return points, "cold_night", date.fromisoformat(data["night_of"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no saved night yet: fetch it

    points, source = get_historical_with_source(lat, lon, *COLD_SEARCH)
    night_of = coldest_night(points) if source != "fallback" else None
    if night_of is None:
        today = date.today()
        return load_fallback(today), "fallback", today

    # Keep the demo window (16:00 -> 10:00) plus an hour either side
    start = datetime.combine(night_of, time(15), tzinfo=LONDON)
    end = datetime.combine(night_of + timedelta(days=1), time(11), tzinfo=LONDON)
    keep = [p for p in points if start <= p.timestamp <= end]
    try:
        COLD_NIGHT_PATH.write_text(json.dumps({
            "night_of": night_of.isoformat(),
            "points": [[p.timestamp.isoformat(), p.temp_c, p.snow] for p in keep],
        }), encoding="utf-8")
    except OSError:
        pass
    return keep, "cold_night", night_of
