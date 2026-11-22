"""Synthetic homes and their temperature curves for the demo.

The physics comes from room_model.simulate(). Realism (sensor noise,
rounding to 0.1°C like the thermometers report, offline sensors) is added
here, on top of the physics.

Each home's rooms are worked out from its energy rating and property type
(homes.room_params_from_epc), so the demo homes double as a check that the
rating mapping gives sensible rooms.
"""
import csv
import dataclasses
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .homes import DEFAULT_START_C, LONDON, NOISE_SIGMA_C, READING_STEP_MIN, build_home
from .room_model import is_heating_on, simulate
from .rules import room_in_use
from .types import ForecastPoint, HeatingWindow, HomeProfile, Reading, Room

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "synthetic_readings.csv"

# Demo homes where someone goes round and turns the heating back on the first
# time the room in use falls to below_c: home_id -> (below_c, setpoint_c).
# Only the synthetic readings know about it. The model has to spot the
# heating coming on from the readings, as it would in a real home.
RESCUES = {"margaret": (16.0, 18.5)}

# Demo homes whose bedroom should reach this temperature at 07:00 with the
# heating off overnight, whatever tonight's forecast (tune_to_forecast).
NIGHT_TARGETS = {"walter": 9.0}
# The bedroom heating goes off at the first of these times that can reach the
# target with a realistic cooling time (tau >= 15 h): later is better for the
# story (a warm evening, then the warnings), earlier is needed on mild nights.
TARGET_OFF_TIMES = [time(20, 30), time(20), time(19, 30), time(19), time(18, 30),
                    time(18), time(17, 30), time(17)]
TAU_RANGE_H = (15.0, 200.0)

# Demo homes' contact settings at the start (the operator can still change them)
DEMO_SETTINGS = {"walter": {"relationship_word": "Dad", "contact_name": "Sue",
                            "night_alerts_opted_in": True}}


def _at(night_of: date, hour: int, minute: int = 0, next_day: bool = False) -> datetime:
    """A London time on the demo night (or the morning after)."""
    day = night_of + timedelta(days=1) if next_day else night_of
    return datetime.combine(day, time(hour, minute), tzinfo=LONDON)


def demo_window(night_of: date) -> tuple[datetime, datetime]:
    """The demo night: 16:00 to 10:00 the next day."""
    return _at(night_of, 16), _at(night_of, 10, next_day=True)


def build_demo_homes(night_of: date | None = None) -> list[HomeProfile]:
    """The demo homes (design doc §6, plus Walter). Event times are set on the night
    of `night_of` (default: today)."""
    night_of = night_of or date.today()
    W = HeatingWindow
    return [
        build_home(
            "margaret", "Margaret", 81, "Cowley", "mid_terrace", sap_score=45,
            schedule={"living": [W(time(7), time(17), 18.5)],
                      "bedroom": [W(time(7), time(17), 18.5)]},
            # Quick radiators, so the climb back to 18°C is easy to see
            room_overrides={"living": {"warmup_rate_c_per_h": 3.0},
                            "bedroom": {"warmup_rate_c_per_h": 3.0}},
            story="Heating off from 17:00 to save money. Daytime check-in, cost card. "
                  "At 16°C someone turns it back on and it's back to 18°C within the hour."),
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
        build_home(
            "walter", "Walter", 86, "Marston", "bungalow", sap_score=30,
            schedule={"living": [W(time(7), time(21), 19.0)],
                      "bedroom": [W(time(7), time(20, 30), 18.5)]},  # tune_to_forecast may move this
            story="Old bungalow, bedroom heating off in the evening and predicted to fall to "
                  "about 9°C by morning. The family is pinged 2 hours before his room drops "
                  "below 16°C, then at night when it's below 10°C."),
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


def tune_to_forecast(homes: list[HomeProfile], forecast: list[ForecastPoint],
                     start: datetime, night_of: date) -> None:
    """For each NIGHT_TARGETS home, pick when its bedroom heating goes off and
    its cooling time, so the bedroom is at the target at 07:00 tonight. Changes
    the home in place (schedule and bedroom RoomParams), so the model predicts
    with the same room the readings come from. `start`: when readings start."""
    seven = _at(night_of, 7, next_day=True)
    for home in homes:
        target = NIGHT_TARGETS.get(home.home_id)
        if target is None:
            continue
        base = home.rooms["bedroom"]
        setpoint = home.schedule["bedroom"][0].setpoint_c

        def at_seven(off: time, tau: float) -> float:
            schedule = [HeatingWindow(time(7), off, setpoint)]
            t_start = setpoint if is_heating_on(start, schedule)[0] else DEFAULT_START_C
            return simulate(t_start, start, seven, dataclasses.replace(base, tau_h=tau),
                            schedule, forecast, READING_STEP_MIN, home.events)[-1][1]

        low, high = TAU_RANGE_H
        off = next((t for t in TARGET_OFF_TIMES if at_seven(t, low) <= target),
                   TARGET_OFF_TIMES[-1])
        if at_seven(off, low) >= target:
            tau = low                        # too mild to reach it: as cold as it gets
        elif at_seven(off, high) <= target:
            tau = high                       # bitterly cold: any room gets there
        else:
            for _ in range(40):              # the 07:00 temperature rises with tau
                mid = (low + high) / 2
                low, high = (mid, high) if at_seven(off, mid) < target else (low, mid)
            tau = (low + high) / 2
        tau = round(tau, 2)
        home.schedule["bedroom"] = [HeatingWindow(time(7), off, setpoint)]
        home.rooms["bedroom"] = dataclasses.replace(base, tau_h=tau)
        home.room_overrides = {**home.room_overrides, "bedroom": {"tau_h": tau}}


ROOM_WORDS = {"living": "living room", "bedroom": "bedroom"}


def demo_beats(home: HomeProfile, forecast: list[ForecastPoint], start: datetime,
               end: datetime, readings_start: datetime) -> list[tuple[datetime, str]]:
    """What happens in this home during the demo night, for the app's notes:
    the heating going on and off, someone turning it back on, a broken boiler,
    a thermometer going quiet. [(time, text)], in time order. Heatify's own
    messages are added by the server from its log."""
    beats = []
    days = (start.date(), start.date() + timedelta(days=1))
    # After a boiler failure the timer still clicks, but nothing comes on or goes off
    broken = min((e["at"] for e in home.events if e["type"] == "boiler_failure"), default=end)
    for room, windows in home.schedule.items():
        for window in windows:
            for day in days:
                on = datetime.combine(day, window.start, tzinfo=start.tzinfo)
                off_day = day if window.end > window.start else day + timedelta(days=1)
                off = datetime.combine(off_day, window.end, tzinfo=start.tzinfo)
                on, off = (on if on < broken else end), (off if off < broken else end)
                if start < on < end:
                    beats.append((on, f"{home.name}'s {ROOM_WORDS[room]} heating comes on "
                                      f"(set to {window.setpoint_c:g}°C)."))
                if start < off < end:
                    beats.append((off, f"{home.name}'s {ROOM_WORDS[room]} heating switches off."))
    rescue = rescue_window(home, forecast, readings_start, end)
    if rescue:
        below_c, _setpoint = RESCUES[home.home_id]
        when = next(datetime.combine(day, rescue.start, tzinfo=start.tzinfo) for day in days
                    if datetime.combine(day, rescue.start, tzinfo=start.tzinfo) >= start)
        beats.append((when, f"The room {home.name} is in has fallen to {below_c:g}°C. Someone "
                            f"goes round and turns the heating back on."))
    for event in home.events:
        if event["type"] == "boiler_failure" and start <= event["at"] < end:
            beats.append((event["at"], f"{home.name}'s boiler breaks down: no heating from now on."))
        if event["type"] == "sensor_offline" and start <= event["from"] < end:
            where = (f"{ROOM_WORDS[event['room']]} thermometer stops" if event.get("room")
                     else "thermometers stop")
            beats.append((event["from"], f"{home.name}'s {where} sending readings."))
    # Two thermometers going quiet at once read better as one note
    texts = {}
    for when, text in beats:
        texts.setdefault(when, []).append(text)
    merged = []
    for when in sorted(texts):
        quiet = [t for t in texts[when] if "thermometer stops" in t]
        if len(quiet) == 2:
            texts[when] = [t for t in texts[when] if t not in quiet] + [
                f"{home.name}'s thermometers stop sending readings."]
        merged.extend((when, text) for text in texts[when])
    return merged


def rescue_window(home: HomeProfile, forecast: list[ForecastPoint],
                  start: datetime, end: datetime) -> HeatingWindow | None:
    """The heating someone turns on for a RESCUES home: from the first step
    where the room in use is at or below below_c, until the end. None if the
    home has no rescue or never gets that cold."""
    if home.home_id not in RESCUES:
        return None
    below_c, setpoint_c = RESCUES[home.home_id]
    sims = {room: simulate(start_temperature(home, room, start), start, end, params,
                           home.schedule.get(room, []), forecast, READING_STEP_MIN,
                           home.events)
            for room, params in home.rooms.items()}
    for i, (when, _t, _e) in enumerate(sims["living"]):
        if sims[room_in_use(when)][i][1] <= below_c:
            return HeatingWindow(when.time(), end.time(), setpoint_c)
    return None


def generate_curves(homes: list[HomeProfile], forecast: list[ForecastPoint],
                    start: datetime, end: datetime,
                    seed: int = 42) -> dict[tuple[str, Room], list[Reading]]:
    """Simulate every room and turn it into noisy, rounded readings."""
    rng = random.Random(seed)  # seeded, so every run gives the same numbers
    curves = {}
    for home in homes:
        rescue = rescue_window(home, forecast, start, end)
        for room, params in home.rooms.items():
            schedule = home.schedule.get(room, []) + ([rescue] if rescue else [])
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
