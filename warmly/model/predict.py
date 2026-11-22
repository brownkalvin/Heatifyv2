"""What the trigger engine calls: overnight predictions, daytime check,
morning summary and weekly outlook.

We don't know whether someone's heating will be on overnight, so live
predictions are always for the heating-off case. The home's schedule is
only used to make synthetic data ("as_scheduled", demo and tests only).

Readings of None (sensor offline) are ignored in every calculation.
"""
from datetime import date, datetime, time, timedelta

from .homes import (BEDTIME, CHECK_IN_AFTER_H, DANGER_FLOOR_C, DAYTIME_WINDOW,
                    FORECAST_ERROR_C, LIKELY_DAYS, LONDON, NIGHT_WINDOW,
                    NO_READING_MAX_AGE_H, TARGET_C, WARMING_SLOPE_C_PER_H)
from .infer_heating import infer_heating, slope_c_per_h
from .room_model import simulate
from .types import (DaytimeStatus, ForecastPoint, HeatingWindow, HomeProfile, NightOutlook,
                    NightSummary, OvernightPrediction, Reading, Room, RoomParams)

SCENARIOS = ("off", "hold_target_overnight", "warm_1h_before_bed", "as_scheduled")


# --------------------------------------------------------------------------- #
# Small time helpers
# --------------------------------------------------------------------------- #
def next_occurrence(now: datetime, clock_time: time) -> datetime:
    """The next time the clock shows `clock_time` after `now`."""
    candidate = now.replace(hour=clock_time.hour, minute=clock_time.minute,
                            second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _hours_before(clock_time: time, hours: float) -> time:
    """E.g. _hours_before(22:00, 1) -> 21:00."""
    moment = datetime.combine(date(2000, 1, 2), clock_time) - timedelta(hours=hours)
    return moment.time()


def night_start(now: datetime) -> datetime:
    """When "overnight" starts for a prediction made at `now`: now, if it's
    already night, otherwise the coming BEDTIME."""
    if now.time() >= BEDTIME or now.time() < NIGHT_WINDOW[1]:
        return now
    return next_occurrence(now, BEDTIME)


def known_events(events: list[dict], now: datetime,
                 include_overrides: bool = True) -> list[dict]:
    """Only the events that have already started by `now`. A prediction made
    at 20:30 can't know the boiler will fail at 01:00.

    include_overrides=False drops "heating_override_off" (the person
    switching their heating off): "what if you turned it on" shouldn't be
    blocked by their own off switch, but a broken boiler still blocks it.
    """
    known = [e for e in events if e.get("at", e.get("from")) <= now]
    if not include_overrides:
        known = [e for e in known if e["type"] != "heating_override_off"]
    return known


# --------------------------------------------------------------------------- #
# Scenarios and overnight prediction
# --------------------------------------------------------------------------- #
def scenario_schedule(home: HomeProfile, room: Room, scenario: str,
                      now: datetime, until: time) -> list[HeatingWindow]:
    """The heating for a scenario. Only "as_scheduled" reads the home's schedule.

    - off:                   no heating from now to `until`
    - hold_target_overnight: hold 18°C from now to `until`
    - warm_1h_before_bed:    heat to 19°C from BEDTIME - 1 h to BEDTIME, then off
    - as_scheduled:          the home's synthetic schedule (demo and tests only)

    There's no "low" setpoint below 18°C on purpose: Warmly never
    recommends a bedroom below 18°C.
    """
    if scenario == "off":
        return []
    if scenario == "hold_target_overnight":
        return [HeatingWindow(now.time(), until, TARGET_C)]
    if scenario == "warm_1h_before_bed":
        return [HeatingWindow(_hours_before(BEDTIME, 1), BEDTIME, TARGET_C + 1)]
    if scenario == "as_scheduled":
        return list(home.schedule.get(room, []))
    raise ValueError(f"unknown scenario {scenario!r}, expected one of {SCENARIOS}")


def run_scenario(home: HomeProfile, room: Room, now: datetime, t_now: float,
                 forecast: list[ForecastPoint], scenario: str,
                 until: time = time(7, 0)) -> list[tuple[datetime, float, float]]:
    """Simulate the room from now until `until` under a scenario.
    Returns simulate()'s rows: (timestamp, t_in, energy_wh)."""
    end = next_occurrence(now, until)
    events = known_events(home.events, now, include_overrides=(scenario == "as_scheduled"))
    return simulate(t_now, now, end, home.rooms[room],
                    scenario_schedule(home, room, scenario, now, until), forecast,
                    events=events)


def predict_if(home: HomeProfile, room: Room, now: datetime, t_now: float,
               forecast: list[ForecastPoint], scenario: str,
               until: time = time(7, 0)) -> OvernightPrediction:
    """Predict the night under a scenario (see scenario_schedule).

    `curve` runs from now to `until`, but the minimum, hours below 18°C and
    will_breach_18 only count the night itself (from BEDTIME).
    """
    sim = run_scenario(home, room, now, t_now, forecast, scenario, until)
    curve = [(when, t) for when, t, _ in sim]

    overnight = [(when, t) for when, t in curve if when >= night_start(now)]
    if not overnight:  # `until` is before bedtime: use the whole curve
        overnight = curve
    min_at, min_c = min(overnight, key=lambda point: point[1])

    seven_am = next_occurrence(now, time(7, 0))
    at_7am = next((t for when, t in curve if when == seven_am), curve[-1][1])

    step_h = (curve[1][0] - curve[0][0]).total_seconds() / 3600 if len(curve) > 1 else 0
    hours_below = sum(step_h for _, t in overnight[1:] if t < TARGET_C)

    return OvernightPrediction(
        home_id=home.home_id, room=room,
        predicted_min_c=min_c, predicted_min_at=min_at,
        predicted_at_7am_c=at_7am, hours_below_18=hours_below,
        will_breach_18=min_c < TARGET_C, curve=curve, scenario=scenario,
    )


def predict_overnight(home: HomeProfile, room: Room, now: datetime, t_now: float,
                      forecast: list[ForecastPoint],
                      until: time = time(7, 0)) -> OvernightPrediction:
    """Will this room fall below 18°C before `until` if the heating stays off?
    Always the heating-off case: same as predict_if(..., scenario="off")."""
    return predict_if(home, room, now, t_now, forecast, "off", until)


def predict_now(home: HomeProfile, room: Room, readings: list[Reading], now: datetime,
                forecast: list[ForecastPoint]) -> OvernightPrediction:
    """The re-anchored heating-off prediction, for the dashboard and family view.

    Restarts from the latest real reading (if it's under NO_READING_MAX_AGE_H
    old) instead of trusting the 20:30 prediction all night. With no such
    reading, assumes the room is at 18°C now and sets assumed_start=True:
    the message must say it's an estimate.
    """
    recent = [r for r in readings if r.temp_c is not None and r.timestamp <= now
              and now - r.timestamp < timedelta(hours=NO_READING_MAX_AGE_H)]
    if recent:
        latest = max(recent, key=lambda r: r.timestamp)
        return predict_overnight(home, room, latest.timestamp, latest.temp_c, forecast)
    prediction = predict_overnight(home, room, now, TARGET_C, forecast)
    prediction.assumed_start = True
    return prediction


def predict_without_reading(home: HomeProfile, room: Room, now: datetime,
                            forecast: list[ForecastPoint],
                            readings: list[Reading] | None = None) -> OvernightPrediction:
    """Forecast-only fallback (design doc §4.8) for a home with no reading,
    e.g. no thermometer yet or Ray's offline gateway. Pass `readings` if you
    have them: a real reading under 2 hours old is used instead."""
    return predict_now(home, room, readings or [], now, forecast)


def predict_crossing(home: HomeProfile, room: Room, now: datetime, t_now: float,
                     forecast: list[ForecastPoint], horizon_h: float = 6) -> datetime | None:
    """When will this room first drop below 18°C in the next `horizon_h`
    hours if the heating stays off? None if it won't, or if it's already
    below. This is the "predicted to fall below 18°C at 18:45" on the
    dashboard: Warmly predicts before it gets cold (design doc §4.9)."""
    if t_now < TARGET_C:
        return None
    sim = simulate(t_now, now, now + timedelta(hours=horizon_h), home.rooms[room], [], forecast)
    return next((when for when, t, _ in sim if t < TARGET_C), None)


# --------------------------------------------------------------------------- #
# Daytime check
# --------------------------------------------------------------------------- #
def _valid(readings: list[Reading]) -> list[Reading]:
    """Readings that have a temperature, oldest first."""
    return sorted((r for r in readings if r.temp_c is not None), key=lambda r: r.timestamp)


def daytime_status(readings: list[Reading], now: datetime,
                   forecast: list[ForecastPoint] | None = None,
                   params: RoomParams | None = None) -> DaytimeStatus:
    """Is the living room cold and not warming up? Pass one room's readings.

    The cold clock starts at 07:00 at the earliest. Otherwise every home that
    cools overnight would get a check-in at 07:00, before the heating has had
    a chance to come on. Pass `forecast` and `params` to also fill in
    heating_on, so the check-in can skip "is your heating on?".
    """
    valid = [r for r in _valid(readings) if r.timestamp <= now]

    # How long has it been below 18°C without a break?
    cold_since = None
    for r in reversed(valid):
        if r.temp_c >= TARGET_C:
            break
        cold_since = r.timestamp
    day_start = now.replace(hour=DAYTIME_WINDOW[0].hour, minute=DAYTIME_WINDOW[0].minute,
                            second=0, microsecond=0)
    if cold_since is not None and now >= day_start:
        cold_since = max(cold_since, day_start)
    below_h = (now - cold_since).total_seconds() / 3600 if cold_since else 0.0

    fit = slope_c_per_h(valid, now)            # (slope, standard error) over 60 minutes
    warming = fit is not None and fit[0] > WARMING_SLOPE_C_PER_H
    heating_on = None
    if forecast is not None and params is not None:
        heating_on = infer_heating(valid, forecast, params, now).heating_on

    daytime = DAYTIME_WINDOW[0] <= now.time() < DAYTIME_WINDOW[1]
    should_check_in = below_h >= CHECK_IN_AFTER_H and not warming and daytime
    return DaytimeStatus(below_h, warming, heating_on, should_check_in)


# --------------------------------------------------------------------------- #
# Morning summary
# --------------------------------------------------------------------------- #
def summarise_night(readings: list[Reading], night_of: date) -> NightSummary:
    """How cold did the room get from 22:00 on `night_of` to 07:00 next morning?"""
    start = datetime.combine(night_of, NIGHT_WINDOW[0], tzinfo=LONDON)
    end = datetime.combine(night_of + timedelta(days=1), NIGHT_WINDOW[1], tzinfo=LONDON)
    in_night = [r for r in readings if start <= r.timestamp <= end]
    valid = _valid(in_night)
    missing = 1 - len(valid) / len(in_night) if in_night else 1.0

    if not valid:
        return NightSummary(night_of, None, None, None, None, 0.0, False, missing)

    coldest = min(valid, key=lambda r: r.temp_c)
    cold = [r for r in valid if r.temp_c < TARGET_C]

    # Time below 18: each gap between readings counts if it starts cold
    hours_below = sum((after.timestamp - before.timestamp).total_seconds() / 3600
                      for before, after in zip(valid, valid[1:])
                      if before.temp_c < TARGET_C)

    return NightSummary(
        night_of=night_of, min_c=coldest.temp_c, min_at=coldest.timestamp,
        cold_from=cold[0].timestamp if cold else None,
        cold_to=cold[-1].timestamp if cold else None,
        hours_below_18=hours_below,
        breached_danger_floor=coldest.temp_c < DANGER_FLOOR_C,
        missing_fraction=missing,
    )


# --------------------------------------------------------------------------- #
# Weekly outlook
# --------------------------------------------------------------------------- #
def _shifted(forecast: list[ForecastPoint], delta_c: float) -> list[ForecastPoint]:
    return [ForecastPoint(p.timestamp, p.temp_c + delta_c, p.snow) for p in forecast]


def predict_week(home: HomeProfile, room: Room, now: datetime, t_now: float,
                 forecast: list[ForecastPoint]) -> list[NightOutlook]:
    """The heating-off case for each of the next 7 nights, BEDTIME to 07:00.

    Night 1 starts from the real reading `t_now` if `now` is within 2 hours
    of bedtime (or already night); otherwise from 18°C. Every later night
    starts from 18°C (assume the room is warm at bedtime), not from the
    night before's minimum.

    Nights the forecast doesn't reach are left out rather than guessed, so
    a short forecast gives fewer than 7 nights.
    """
    morning = NIGHT_WINDOW[1]
    first_night = now.date() - timedelta(days=1) if now.time() < morning else now.date()
    near_bedtime = now.time() >= _hours_before(BEDTIME, 2) or now.time() < morning
    params = home.rooms[room]
    outlook = []
    for days_ahead in range(1, 8):
        night_of = first_night + timedelta(days=days_ahead - 1)
        start = datetime.combine(night_of, BEDTIME, tzinfo=LONDON)
        end = datetime.combine(night_of + timedelta(days=1), morning, tzinfo=LONDON)
        t_start = TARGET_C
        if days_ahead == 1 and near_bedtime:
            start, t_start = now, t_now
        if not forecast or forecast[0].timestamp > start or forecast[-1].timestamp < end:
            continue

        def night_min(delta_c: float) -> float:
            sim = simulate(t_start, start, end, params, [], _shifted(forecast, delta_c))
            return min(t for _, t, _ in sim)

        error = FORECAST_ERROR_C[days_ahead]
        night = [p for p in forecast if start <= p.timestamp <= end]
        outlook.append(NightOutlook(
            night_of=night_of, days_ahead=days_ahead,
            predicted_min_c=night_min(0.0),
            range_c=(night_min(-error), night_min(error)),
            confidence="likely" if days_ahead <= LIKELY_DAYS else "possible",
            snow=any(p.snow for p in night),
            freezing=any(p.temp_c < 0 for p in night),
        ))
    return outlook
