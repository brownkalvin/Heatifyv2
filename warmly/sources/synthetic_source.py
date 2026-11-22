"""Readings from the synthetic demo homes, at the simulated clock's time."""
from datetime import datetime, timedelta

from warmly.model.synthetic import generate_curves
from warmly.model.types import ForecastPoint, HomeProfile, Reading, Room

from .base import SimClock


class SyntheticSource:
    """Precomputes every home's curves, then serves the reading at clock.now().

    Noise, rounding and offline sensors are applied by generate_curves().
    """

    def __init__(self, homes: list[HomeProfile], forecast: list[ForecastPoint],
                 clock: SimClock, start: datetime | None = None,
                 end: datetime | None = None, seed: int = 42):
        self.clock = clock
        start = start or clock.start
        end = end or start + timedelta(hours=18)  # 16:00 -> 10:00 next day
        self.curves = generate_curves(homes, forecast, start, end, seed)

    def get_reading(self, home_id: str, room: Room) -> Reading:
        """The latest reading at or before the clock's time.
        Before the curves start, returns a reading with temp_c=None."""
        now = self.clock.now()
        readings = self.curves[(home_id, room)]
        latest = None
        for reading in readings:
            if reading.timestamp > now:
                break
            latest = reading
        if latest is None:
            return Reading(home_id, room, now, None, "synthetic")
        return latest

    def get_history(self, home_id: str, room: Room, since: datetime) -> list[Reading]:
        """All readings from `since` up to the clock's time."""
        now = self.clock.now()
        return [r for r in self.curves[(home_id, room)] if since <= r.timestamp <= now]
