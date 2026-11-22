"""The reading-source interface and the simulated clock.

The dashboard and trigger engine must take time from a clock, never from
datetime.now(), so the demo can run a whole night in a few minutes.
"""
import json
import time as real_time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from warmly.model.types import Reading, Room


class ReadingSource(Protocol):
    """Anything that can give thermometer readings: SyntheticSource for the
    demo, StoredSource for real sensors (whichever gateway fills the store).
    (The pitch doc said get_reading(home_id); we added `room` because every
    home has two thermometers.)"""

    def get_reading(self, home_id: str, room: Room) -> Reading: ...

    def get_history(self, home_id: str, room: Room, since: datetime) -> list[Reading]: ...


SENSORS_PATH = Path(__file__).with_name("sensors.json")
SENSOR_TYPES = ("switchbot", "esp32")


def load_sensors(path: str | Path = SENSORS_PATH) -> list[dict]:
    """Which physical thermometer is in which home and room. The ONLY place
    that ties a sensor to a home. Each entry:
    {"id": "...", "type": "switchbot" | "esp32", "home_id": "...", "room": "living" | "bedroom"}
    The entries shipped in sensors.json are examples: replace them with real ids.
    """
    sensors = json.loads(Path(path).read_text(encoding="utf-8"))
    for sensor in sensors:
        if sensor["type"] not in SENSOR_TYPES:
            raise ValueError(f"unknown sensor type {sensor['type']!r}, expected {SENSOR_TYPES}")
        if sensor["room"] not in ("living", "bedroom"):
            raise ValueError(f"unknown room {sensor['room']!r}")
    return sensors


class RealClock:
    """Real time, for live mode with real people and real sensors. Same
    methods as SimClock, so the server treats both alike; pausing does
    nothing, because real people take real time to reply."""

    speed = 1

    def __init__(self) -> None:
        from warmly.model.homes import LONDON   # imported here to keep this file light
        self._zone = LONDON

    def now(self) -> datetime:
        """The current time in Europe/London."""
        return datetime.now(self._zone)

    @property
    def paused(self) -> bool:
        return False

    def pause(self) -> None:
        """Real time can't be paused."""

    def pause_for_message(self) -> None:
        """Real time can't be paused."""

    def resume(self) -> None:
        """Real time can't be paused."""


class SimClock:
    """A clock that runs faster than real time.

    speed=720 means 1 simulated hour passes every 5 real seconds.
    """

    def __init__(self, start: datetime, speed: float = 720):
        self.start = start
        self.speed = speed
        self._sim_anchor = start                    # simulated time at the anchor
        self._real_anchor = real_time.monotonic()   # real time at the anchor
        self._paused = False

    def now(self) -> datetime:
        """The current simulated time."""
        if self._paused:
            return self._sim_anchor
        real_elapsed_s = real_time.monotonic() - self._real_anchor
        return self._sim_anchor + timedelta(seconds=real_elapsed_s * self.speed)

    def pause(self) -> None:
        """Freeze simulated time."""
        self._sim_anchor = self.now()
        self._paused = True

    def pause_for_message(self) -> None:
        """Called by the backend whenever it sends the person a message.

        At 720x a 30-second reply would be 6 simulated hours, so time stops
        until resume() (the reply arrives, or the operator presses "continue").
        Escalation timers measured in simulated time freeze with it.
        """
        self.pause()

    @property
    def paused(self) -> bool:
        """True while simulated time is frozen."""
        return self._paused

    def resume(self) -> None:
        """Carry on from where it was paused."""
        self._real_anchor = real_time.monotonic()
        self._paused = False

    def jump_to(self, when: datetime) -> None:
        """Move simulated time straight to `when` (stays paused if paused)."""
        self._sim_anchor = when
        self._real_anchor = real_time.monotonic()
