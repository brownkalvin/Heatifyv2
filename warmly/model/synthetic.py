"""Synthetic homes and their temperature curves for the demo.

The physics comes from room_model.simulate(). Realism (sensor noise,
rounding to 0.1°C like the thermometers report, offline sensors) is added
here, on top of the physics.

Each home's rooms are worked out from its energy rating and property type
(homes.room_params_from_epc), so the demo homes double as a check that the
rating mapping gives sensible rooms.
"""
import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .homes import DEFAULT_START_C, LONDON, NOISE_SIGMA_C, READING_STEP_MIN, build_home
from .room_model import is_heating_on, simulate
from .types import ForecastPoint, HeatingWindow, HomeProfile, Reading, Room

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "synthetic_readings.csv"


def _at(night_of: date, hour: int, minute: int = 0, next_day: bool = False) -> datetime:
    """A London time on the demo night (or the morning after)."""
    day = night_of + timedelta(days=1) if next_day else night_of
    return datetime.combine(day, time(hour, minute), tzinfo=LONDON)


def demo_window(night_of: date) -> tuple[datetime, datetime]:
    """The demo night: 16:00 to 10:00 the next day."""
    return _at(night_of, 16), _at(night_of, 10, next_day=True)


def build_demo_homes(night_of: date | None = None) -> list[HomeProfile]:
    """The six demo homes (design doc §6). Event times are set on the night
    of `night_of` (default: today)."""
    night_of = night_of or date.today()
    W = HeatingWindow
    return [
        build_home(
            "margaret", "Margaret", 81, "Cowley", "mid_terrace", sap_score=45,
            schedule={"living": [W(time(7), time(17), 18.5)],
                      "bedroom": [W(time(7), time(17), 18.5)]},
            story="Heating off from 17:00 to save money. Daytime check-in, cost card."),
        build_home(
            "frank", "Frank", 77, "Headington", "semi", sap_score=60,
            schedule={"living": [W(time(7), time(22, 30), 20.0)],
                      "bedroom": [W(time(16), time(8), 18.5)]},
            story="Heating on as normal. Stays green."),
        build_home(
            "joan", "Joan", 85, "Jericho", "flat", sap_score=72,
            schedule={"living": [W(time(7), time(23), 20.0)],
                      "bedroom": [W(time(16), time(7), 18.5)]},
            events=[{"type": "boiler_failure", "at": _at(night_of, 1, next_day=True)}],
            story="Boiler fails at 01:00. Slow drop, morning check-in."),
        build_home(
            "ray", "Ray", 79, "Blackbird Leys", "semi", sap_score=60,
            schedule={"living": [W(time(7), time(22), 19.0)],
                      "bedroom": [W(time(21), time(23), 19.0)]},
            events=[{"type": "sensor_offline", "room": "living", "from": _at(night_of, 19)},
                    {"type": "sensor_offline", "room": "bedroom", "from": _at(night_of, 19)}],
            story="Thermometers go offline at 19:00. Shows the 'no reading' fallback."),
        build_home(
            "pat", "Pat", 83, "Botley", "flat", sap_score=72,
            schedule={"living": [W(time(16), time(22), 19.0)],
                      "bedroom": [W(time(21), time(22), 19.0)]},
            story="Warms the bedroom before bed: the cheaper middle way."),
        build_home(
            "eileen", "Eileen", 88, "Barton", "flat", sap_score=72,
            schedule={"living": [W(time(7), time(21), 19.0)],
                      "bedroom": [W(time(16), time(20), 18.0)]},
            room_overrides={"bedroom": {"tau_h": 25, "heat_loss_w_per_c": 50}},
            story="Bedroom window left ajar, heating off at 20:00. Bedtime message: "
                  "about 12°C by morning."),
    ]


def start_temperature(home: HomeProfile, room: Room, when: datetime) -> float:
    """The room's setpoint if heating is on at `when`, otherwise 18.5°C."""
    heating_on, setpoint = is_heating_on(when, home.schedule.get(room, []))
    return setpoint if heating_on else DEFAULT_START_C


def sensor_offline(when: datetime, room: Room, events: list[dict]) -> bool:
    """True if a sensor_offline event covers this room at `when`.

    Event: {"type": "sensor_offline", "room": "living"|"bedroom", "from": datetime,
            "to": datetime (optional)}. With no "room" it covers both rooms.
    """
    for event in events:
        if event["type"] != "sensor_offline":
            continue
        if event.get("room") not in (None, room):
            continue
        if event["from"] <= when and ("to" not in event or when < event["to"]):
            return True
    return False


def generate_curves(homes: list[HomeProfile], forecast: list[ForecastPoint],
                    start: datetime, end: datetime,
                    seed: int = 42) -> dict[tuple[str, Room], list[Reading]]:
    """Simulate every room and turn it into noisy, rounded readings."""
    rng = random.Random(seed)  # seeded, so every run gives the same numbers
    curves = {}
    for home in homes:
        for room, params in home.rooms.items():
            schedule = home.schedule.get(room, [])
            t_start = start_temperature(home, room, start)
            sim = simulate(t_start, start, end, params, schedule, forecast,
                           READING_STEP_MIN, home.events)
            readings = []
            for when, t_in, _energy in sim:
                if sensor_offline(when, room, home.events):
                    temp = None
                else:
                    temp = round(t_in + rng.gauss(0, NOISE_SIGMA_C), 1)
                readings.append(Reading(home.home_id, room, when, temp, "synthetic"))
            curves[(home.home_id, room)] = readings
    return curves


def export_csv(curves: dict[tuple[str, Room], list[Reading]],
               path: str | Path = DEFAULT_CSV) -> Path:
    """Write all readings to a CSV (empty temp_c = no reading)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["home_id", "room", "timestamp", "temp_c", "source"])
        for readings in curves.values():
            for r in readings:
                writer.writerow([r.home_id, r.room, r.timestamp.isoformat(),
                                 "" if r.temp_c is None else r.temp_c, r.source])
    return path
