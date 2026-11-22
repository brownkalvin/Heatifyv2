"""The three endpoints the family app (web/) expects, built from the model.

The app (UI_SPEC.md §5) shows ONE home to a family member:
    readings(...)     -> [{time, temperature}]                          GET /readings
    live_status(...)  -> {heatingOn, projectionOff, projectionOn, asOf}  GET /live-status
    week_forecast(...)-> {generatedAt, hourly: [{time, predictedTemp}]}  GET /forecast

It has one thermometer's worth of UI, so it follows the room that matters
right now (rules.room_in_use): the living room 07:00-22:00, the bedroom at
night. Everything is relative to the server's clock (the demo's fast clock),
not the browser's.
"""
import bisect
import random
from datetime import date, datetime, time, timedelta

from warmly.model.homes import BEDTIME, DANGER_FLOOR_C, DAYTIME_WINDOW, TARGET_C
from warmly.model.infer_heating import infer_heating
from warmly.model.predict import predict_now
from warmly.model.room_model import simulate
from warmly.model.rules import room_in_use
from warmly.model.types import ForecastPoint, HeatingWindow, HomeProfile, Reading, Room

PROJECTION_H = 6
WEEK_H = 168
# "If the heating stays on": the thermostat holds the room where it is, or
# warms it to this if it's colder (we don't know the real setting, so we
# never predict a warm room getting warmer)
HEATING_ON_MIN_SETPOINT_C = TARGET_C + 0.5


def all_day(setpoint_c: float) -> list[HeatingWindow]:
    """The whole day, as two windows (a single start == end window means "never")."""
    return [HeatingWindow(time(12), time(12, 0, 0, 1), setpoint_c),
            HeatingWindow(time(12, 0, 0, 1), time(12), setpoint_c)]
# The week view's "usual pattern": warm (18°C) through the day until bedtime,
# off overnight. The same assumption predict_week makes for each night.
USUAL_DAY = [HeatingWindow(DAYTIME_WINDOW[0], BEDTIME, TARGET_C)]
# Real days aren't identical: the heating comes on and goes off at slightly
# different times, and the thermostat isn't always at exactly 18°C. Each day
# gets its own small, repeatable (seeded by home + date) wobble on USUAL_DAY.
ON_JITTER_MIN = (-40, 35)        # heating on: ~06:20 - 07:35
OFF_JITTER_MIN = (-110, 55)      # heating off: ~20:10 - 22:55
SETPOINT_JITTER_C = (0.0, 1.0)   # 18.0 - 19.0°C, so it never starts the night cold


def _shift(t: time, minutes: int) -> time:
    return (datetime.combine(date.min, t) + timedelta(minutes=minutes)).time()


def usual_day_for(home_id: str, day: date) -> list[HeatingWindow]:
    """USUAL_DAY with this day's wobble on the on/off times and the setpoint."""
    rng = random.Random(f"{home_id}:{day.isoformat()}")
    on = _shift(DAYTIME_WINDOW[0], 5 * round(rng.randint(*ON_JITTER_MIN) / 5))
    off = _shift(BEDTIME, 5 * round(rng.randint(*OFF_JITTER_MIN) / 5))
    setpoint = round(TARGET_C + rng.uniform(*SETPOINT_JITTER_C), 1)
    return [HeatingWindow(on, off, setpoint)]


def _simulate_days(t_start: float, start: datetime, end: datetime, params, home_id: str,
                   forecast: list[ForecastPoint]) -> list[tuple[datetime, float]]:
    """simulate(), one day at a time, each day with its own usual_day_for()."""
    out = [(start, t_start)]
    t, seg_start = t_start, start
    while seg_start < end:
        midnight = datetime.combine(seg_start.date() + timedelta(days=1), time(), seg_start.tzinfo)
        seg_end = min(midnight, end)
        sim = simulate(t, seg_start, seg_end, params,
                       usual_day_for(home_id, seg_start.date()), forecast)
        if sim[-1][0] < seg_end:                 # finish a part step up to midnight
            last = sim[-1][0]
            sim += simulate(sim[-1][1], last, seg_end, params,
                            usual_day_for(home_id, seg_start.date()), forecast,
                            dt_min=max(1, int((seg_end - last).total_seconds() // 60)))[1:]
        out += [(when, temp) for when, temp, _ in sim[1:]]
        t, seg_start = out[-1][1], seg_end
    return out


def _interp(series: list[tuple[datetime, float]], when: datetime) -> float:
    times = [w for w, _ in series]
    i = bisect.bisect_left(times, when)
    if i <= 0:
        return series[0][1]
    if i >= len(series):
        return series[-1][1]
    (t0, v0), (t1, v1) = series[i - 1], series[i]
    f = (when - t0) / (t1 - t0) if t1 > t0 else 0.0
    return v0 + f * (v1 - v0)


def _point(when: datetime, temp: float, key: str = "temperature") -> dict:
    return {"time": when.isoformat(), key: round(temp, 2)}


def _valid(readings: list[Reading], now: datetime) -> list[Reading]:
    return [r for r in readings if r.timestamp <= now and r.temp_c is not None]


def room_now(now: datetime) -> Room:
    return room_in_use(now)


def readings(home: HomeProfile, now: datetime, source) -> list[dict]:
    """The last 24 h of the room in use."""
    history = source.get_history(home.home_id, room_now(now), now - timedelta(hours=24))
    return [_point(r.timestamp, r.temp_c) for r in _valid(history, now)]


def live_status(home: HomeProfile, now: datetime, source,
                forecast: list[ForecastPoint]) -> dict:
    """Is the heating on, and the next 6 h with the heating off and on."""
    room = room_now(now)
    history = _valid(source.get_history(home.home_id, room, now - timedelta(hours=24)), now)
    status = {"room": room, "asOf": now.isoformat(), "heatingOn": False,
              "projectionOff": [], "projectionOn": [],
              "tonight": tonight(home, now, source, forecast)}
    if not history:
        return status
    latest = history[-1]
    params = home.rooms[room]
    status["heatingOn"] = infer_heating(history, forecast, params, now).heating_on is True
    end = latest.timestamp + timedelta(hours=PROJECTION_H)
    held = all_day(max(latest.temp_c, HEATING_ON_MIN_SETPOINT_C))
    for key, schedule in (("projectionOff", []), ("projectionOn", held)):
        sim = simulate(latest.temp_c, latest.timestamp, end, params, schedule, forecast)
        status[key] = [_point(when, t) for when, t, _ in sim[1:]]
    return status


def tonight(home: HomeProfile, now: datetime, source, forecast: list[ForecastPoint]) -> dict | None:
    """The bedroom tonight if the heating stays off (or goes off now), from
    17:00 to 07:00; None in the day. The same prediction as the bedtime
    message, re-anchored to the latest bedroom reading."""
    if DAYTIME_WINDOW[0] <= now.time() < time(17):
        return None
    history = _valid(source.get_history(home.home_id, "bedroom", now - timedelta(hours=3)), now)
    pred = predict_now(home, "bedroom", history, now, forecast)
    heating = infer_heating(history, forecast, home.rooms["bedroom"], now).heating_on if history else None
    return {"predictedMin": round(pred.predicted_min_c, 2),
            "predictedMinAt": pred.predicted_min_at.isoformat(),
            "at7am": round(pred.predicted_at_7am_c, 2),
            "assumedStart": pred.assumed_start,
            "heatingOn": heating is True,
            "dangerFloor": DANGER_FLOOR_C,
            "curve": [_point(when, t) for when, t in pred.curve]}


def week_forecast(home: HomeProfile, now: datetime, source,
                  forecast: list[ForecastPoint]) -> dict:
    """Hourly predicted temperature of the room in use, on the hour, for as much
    of the next 7 days as the weather forecast covers, assuming a usual day
    (usual_day_for: USUAL_DAY with a little day-to-day variation)."""
    hourly: list[dict] = []
    end = min(now + timedelta(hours=WEEK_H), forecast[-1].timestamp) if forecast else now
    if end > now:
        sims = {}
        for room in ("living", "bedroom"):
            history = _valid(source.get_history(home.home_id, room, now - timedelta(hours=6)), now)
            t_start = history[-1].temp_c if history else TARGET_C
            sims[room] = _simulate_days(t_start, now, end, home.rooms[room], home.home_id, forecast)
        when = now.replace(minute=0, second=0, microsecond=0)
        if when < now:
            when += timedelta(hours=1)
        while when <= end:
            hourly.append(_point(when, _interp(sims[room_now(when)], when), "predictedTemp"))
            when += timedelta(hours=1)
    return {"generatedAt": now.isoformat(), "hourly": hourly}
