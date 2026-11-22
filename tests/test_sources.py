import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from warmly.sources import SimClock, StoredSource, load_sensors
from warmly.sources.stored_source import CREATE_TABLE
from helpers import at


def test_clock_pause(monkeypatch):
    """Simulated time doesn't advance while paused for a message, so an
    escalation timer measured in simulated time is frozen too."""
    import warmly.sources.base as base
    fake_now = [1000.0]
    monkeypatch.setattr(base.real_time, "monotonic", lambda: fake_now[0])

    clock = SimClock(at(22, 20))
    sent_at = clock.now()
    clock.pause_for_message()
    assert clock.paused
    fake_now[0] += 30                       # 30 real seconds waiting for a reply
    assert clock.now() - sent_at == timedelta(0)
    clock.resume()
    fake_now[0] += 5
    assert clock.now() == at(22, 21)        # carries on from where it stopped


def _store(tmp_path, rows):
    db = tmp_path / "readings.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(CREATE_TABLE)
        conn.executemany("INSERT INTO readings VALUES (?, ?, ?)", rows)
        conn.commit()
    return db


SENSORS = [{"id": "esp-1", "type": "esp32", "home_id": "joan", "room": "bedroom"},
           {"id": "ble-1", "type": "switchbot", "home_id": "joan", "room": "living"}]


def test_stored_source(tmp_path):
    db = _store(tmp_path, [
        ("esp-1", at(22, 20).isoformat(), 17.4),
        ("esp-1", "2026-11-22T20:15:00", 17.3),      # no UTC offset: rejected
        ("esp-1", at(22, 21).isoformat(), 17.0),     # after the clock: not yet
        ("ble-1", at(22, 20).isoformat(), 16.0),
    ])
    clock = SimClock(at(22, 20, 30))
    clock.pause()
    source = StoredSource(db, clock, SENSORS)

    reading = source.get_reading("joan", "bedroom")
    assert (reading.timestamp, reading.temp_c, reading.source) == (at(22, 20), 17.4, "esp32")
    assert [r.temp_c for r in source.get_history("joan", "bedroom", at(22, 19))] == [17.4]
    assert source.get_reading("joan", "living").source == "switchbot"
    assert source.get_reading("frank", "bedroom").temp_c is None   # no sensor mapped


def test_clean_spike_and_stale(tmp_path):
    """A +5°C single spike is dropped; a steady 3°C fall over 3 h is kept;
    newest reading 50 minutes old -> no reading."""
    from datetime import timedelta
    from warmly.model.types import Reading
    from warmly.sources.stored_source import clean_readings

    falling = [Reading("joan", "bedroom", at(22, 20) + timedelta(minutes=15 * i),
                       round(18.0 - 0.25 * i, 2), "esp32") for i in range(13)]   # 18 -> 15°C
    spiked = list(falling)
    spiked[6] = Reading("joan", "bedroom", spiked[6].timestamp, spiked[6].temp_c + 5, "esp32")
    out_of_range = Reading("joan", "bedroom", at(22, 20, 7), 85.0, "esp32")
    duplicate = Reading("joan", "bedroom", at(22, 20, 0) + timedelta(seconds=30), 18.0, "esp32")

    assert clean_readings(falling, at(23, 0)) == falling
    cleaned = clean_readings(spiked + [out_of_range, duplicate], at(23, 0))
    assert len(cleaned) == 12 and spiked[6] not in cleaned
    assert [r.timestamp for r in cleaned] == sorted(r.timestamp for r in cleaned)

    db = _store(tmp_path, [("esp-1", at(22, 19, 40).isoformat(), 17.0)])
    clock = SimClock(at(22, 20, 30))
    clock.pause()
    assert StoredSource(db, clock, SENSORS).get_reading("joan", "bedroom").temp_c is None
    clock.jump_to(at(22, 20, 20))                     # 40 minutes old: still counts
    assert StoredSource(db, clock, SENSORS).get_reading("joan", "bedroom").temp_c == 17.0


def test_shipped_sensor_config_loads():
    assert {s["type"] for s in load_sensors()} == {"switchbot", "esp32"}


def test_rejects_unknown_sensor(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps([{"id": "x", "type": "dht11", "home_id": "h", "room": "living"}]))
    with pytest.raises(ValueError):
        load_sensors(path)
