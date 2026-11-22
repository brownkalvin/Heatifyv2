"""Small helpers shared by the tests."""
from datetime import datetime, timedelta

from warmly.model.homes import LONDON
from warmly.model.types import ForecastPoint


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A London time on day `day` of Nov 2026."""
    return datetime(2026, 11, day, hour, minute, tzinfo=LONDON)


def constant_forecast(temp_c: float, start: datetime, hours: int = 48) -> list[ForecastPoint]:
    """Hourly forecast with the same outdoor temperature throughout."""
    return [ForecastPoint(start + timedelta(hours=i), temp_c) for i in range(hours + 1)]
