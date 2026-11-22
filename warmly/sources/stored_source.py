"""Real readings, from the shared SQLite store (design doc §4.6, §4.12).

Both sensor options write into one table, so only the gateway differs:
- SwitchBot: a laptop script reads Bluetooth and writes rows.
- ESP32: posts {"sensor_id", "temp_c", "ts"} to server.py's /api/reading,
  which writes rows.

Table: readings(sensor_id TEXT, ts TEXT ISO-8601 with offset, temp_c REAL)
sensors.json maps each sensor_id to a home and room.

Real sensors are messy, so every batch goes through clean_readings() first.
"""
import logging
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from warmly.model.homes import LONDON, READING_RANGE_C, SPIKE_C, STALE_AFTER_MIN
from warmly.model.types import Reading, Room

from .base import SimClock, load_sensors

CREATE_TABLE = "CREATE TABLE IF NOT EXISTS readings (sensor_id TEXT, ts TEXT, temp_c REAL)"

log = logging.getLogger("warmly.sources")


def clean_readings(raw: list[Reading], now: datetime) -> list[Reading]:
    """Tidy one room's readings before anything else sees them.

    - Drops readings after `now`, and anything outside -10°C to 40°C (a fault).
    - Keeps one reading per minute (the last one), sorted by time.
    - Drops spikes: a reading more than SPIKE_C from the median of its 2
      neighbours on each side (someone picked it up, a kettle, the sun).
      A real sustained change, like a boiler failure's steady fall, passes.
    """
    low, high = READING_RANGE_C
    usable = sorted((r for r in raw if r.timestamp <= now
                     and (r.temp_c is None or low <= r.temp_c <= high)),
                    key=lambda r: r.timestamp)
    per_minute = {r.timestamp.replace(second=0, microsecond=0): r for r in usable}
    ordered = list(per_minute.values())

    valid = [r for r in ordered if r.temp_c is not None]
    spikes = set()
    for i, reading in enumerate(valid):
        neighbours = [r.temp_c for r in valid[max(0, i - 2):i] + valid[i + 1:i + 3]]
        if neighbours and abs(reading.temp_c - statistics.median(neighbours)) > SPIKE_C:
            spikes.add(id(reading))
    return [r for r in ordered if id(r) not in spikes]


class StoredSource:
    """Reads the store and serves cleaned readings at the clock's time."""

    def __init__(self, db_path: str | Path, clock: SimClock, sensors: list[dict] | None = None):
        self.db_path = Path(db_path)
        self.clock = clock
        sensors = load_sensors() if sensors is None else sensors
        self._sensor_for = {(s["home_id"], s["room"]): s for s in sensors}

    def _raw(self, home_id: str, room: Room) -> list[Reading]:
        """Every stored reading for this room's sensor. Naive timestamps
        (no UTC offset) are rejected with a warning."""
        sensor = self._sensor_for.get((home_id, room))
        if sensor is None or not self.db_path.exists():
            return []
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute("SELECT ts, temp_c FROM readings WHERE sensor_id = ?",
                                (sensor["id"],)).fetchall()
        readings = []
        for ts, temp_c in rows:
            try:
                when = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                log.warning("sensor %s: unreadable timestamp %r, skipped", sensor["id"], ts)
                continue
            if when.tzinfo is None:
                log.warning("sensor %s: timestamp %s has no UTC offset, skipped", sensor["id"], ts)
                continue
            readings.append(Reading(home_id, room, when.astimezone(LONDON),
                                    None if temp_c is None else float(temp_c), sensor["type"]))
        return readings

    def get_reading(self, home_id: str, room: Room) -> Reading:
        """The latest clean reading at or before the clock's time. temp_c is
        None if there isn't one, or if it's more than STALE_AFTER_MIN old: a
        real sensor going quiet looks exactly like Ray's offline sensor."""
        now = self.clock.now()
        sensor_type = self._sensor_for.get((home_id, room), {}).get("type", "unknown")
        valid = [r for r in clean_readings(self._raw(home_id, room), now) if r.temp_c is not None]
        if not valid or now - valid[-1].timestamp > timedelta(minutes=STALE_AFTER_MIN):
            return Reading(home_id, room, now, None, sensor_type)
        return valid[-1]

    def get_history(self, home_id: str, room: Room, since: datetime) -> list[Reading]:
        """All clean readings from `since` up to the clock's time."""
        now = self.clock.now()
        return [r for r in clean_readings(self._raw(home_id, room), now) if since <= r.timestamp]
