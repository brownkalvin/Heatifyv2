"""Is the heating on? Worked out from the readings (design doc §4.3b).

With the heating off, a room cools towards outside at

    expected = (T_out - T_in) / tau          °C per hour (negative on a cold night)

Over the last 90 minutes we fit the thermometer's slope and its standard
error `se` (never below 0.05°C/h), then:

- |expected| < 2·se: too mild to tell on from off  ->  None
- heating ON if  slope - expected > 2·se  AND  slope > expected / 2
  (the room is cooling less than half as fast as free cooling predicts)
- otherwise OFF

Before that, a quicker check: if the room is warmer than outside, is
RISE_MIN_C (0.5°C) or more above its lowest reading of the last 90 minutes,
and hasn't fallen in the last 30, the heating is ON. An unheated room can't
warm up on a cold night, and 0.5°C is about 4 times the noise between two
readings. "Hasn't fallen" keeps it on while the room holds at its setpoint
after warming up. Without it, a room that cooled and
then warmed (the heating coming back on) is a V in the 90-minute window:
the straight-line fit is too unsure to tell for about an hour.

Why not a fixed "residual > 0.3°C/h": a radiator HOLDING a flat steady only
shows a residual of about 0.25°C/h (Joan), so the fixed rule called it off.
This rule scales with how fast the room should be cooling and with the noise.
Why 90 minutes, not 60: with sensor noise and 0.1°C rounding, 60 minutes
caught a held flat only 61% of the time; 90 minutes catches it about 96%.

slope_c_per_h() lives here because the daytime "warming" check uses it too.
"""
import math
import statistics
from datetime import date, datetime, timedelta

from .forecast import temp_at
from .homes import (INFER_MIN_READINGS, INFER_WINDOW_MIN, LONDON, NIGHT_WINDOW,
                    RISE_MIN_C, RISE_WINDOW_MIN, SLOPE_MIN_READINGS, SLOPE_SE_FLOOR,
                    SLOPE_WINDOW_MIN)
from .types import ForecastPoint, HeatingInference, Reading, RoomParams

NOT_FALLING_SLACK_C = 0.1     # "hasn't fallen" allows for one step of noise or rounding


def _window(readings: list[Reading], now: datetime, window_min: float) -> list[Reading]:
    """Readings with a temperature in the last `window_min` minutes up to `now`, oldest first."""
    since = now - timedelta(minutes=window_min)
    return sorted((r for r in readings
                   if r.temp_c is not None and since <= r.timestamp <= now),
                  key=lambda r: r.timestamp)


def slope_c_per_h(readings: list[Reading], now: datetime,
                  window_min: float = SLOPE_WINDOW_MIN,
                  min_readings: int = SLOPE_MIN_READINGS) -> tuple[float, float] | None:
    """Least-squares slope over the last `window_min` minutes, in °C per hour,
    and its standard error: (slope, standard_error).
    None if there are fewer than `min_readings` readings."""
    window = _window(readings, now, window_min)
    if len(window) < max(min_readings, 3):
        return None
    hours = [(r.timestamp - window[0].timestamp).total_seconds() / 3600 for r in window]
    temps = [r.temp_c for r in window]
    mean_h = statistics.fmean(hours)
    spread = sum((h - mean_h) ** 2 for h in hours)
    if spread == 0:
        return None
    slope, intercept = statistics.linear_regression(hours, temps)
    misfit = sum((t - (intercept + slope * h)) ** 2 for h, t in zip(hours, temps))
    standard_error = math.sqrt(misfit / (len(window) - 2) / spread)
    return slope, standard_error


def clear_rise(readings: list[Reading], forecast: list[ForecastPoint],
               params: RoomParams, now: datetime) -> HeatingInference | None:
    """Heating ON if, while it's colder outside than the room, the room is
    RISE_MIN_C or more above its lowest reading of the last 90 minutes and
    hasn't fallen over the last RISE_WINDOW_MIN (30) minutes; otherwise None
    (not decided). The windows get 10 minutes' slack, so a clock a moment past
    the quarter-hour doesn't drop the oldest reading."""
    window = _window(readings, now, INFER_WINDOW_MIN + 10)
    recent = _window(readings, now, RISE_WINDOW_MIN + 10)[-3:]
    if len(recent) < 3:
        return None
    latest = recent[-1]
    low = min(window[:-1], key=lambda r: r.temp_c)
    t_out = temp_at(forecast, now)
    if (latest.temp_c - low.temp_c < RISE_MIN_C or t_out >= latest.temp_c
            or latest.temp_c < recent[0].temp_c - NOT_FALLING_SLACK_C):
        return None
    hours = (latest.timestamp - low.timestamp).total_seconds() / 3600
    expected = (t_out - low.temp_c) / params.tau_h
    residual = (latest.temp_c - low.temp_c) / hours - expected
    return HeatingInference(True, residual, residual * params.heat_loss_w_per_c * params.tau_h)


def infer_heating(readings: list[Reading], forecast: list[ForecastPoint],
                  params: RoomParams, now: datetime) -> HeatingInference:
    """Is the heating on in this room at `now`? Pass one room's readings.
    heating_on is None if there aren't enough readings or it's too mild to tell."""
    rising = clear_rise(readings, forecast, params, now)
    if rising is not None:
        return rising
    fit = slope_c_per_h(readings, now, INFER_WINDOW_MIN, INFER_MIN_READINGS)
    if fit is None:
        return HeatingInference(None, None, None)
    slope, se = fit
    se = max(se, SLOPE_SE_FLOOR)

    t_in = statistics.fmean(r.temp_c for r in _window(readings, now, INFER_WINDOW_MIN))
    t_out = temp_at(forecast, now - timedelta(minutes=INFER_WINDOW_MIN / 2))
    expected = (t_out - t_in) / params.tau_h
    residual = slope - expected
    if abs(expected) < 2 * se:
        return HeatingInference(None, residual, None)   # too mild to tell

    heating_on = residual > 2 * se and slope > expected / 2
    # Rough: from the P_heat / (H x tau) term in room_model's docstring
    power_w = residual * params.heat_loss_w_per_c * params.tau_h if heating_on else None
    return HeatingInference(heating_on, residual, power_w)


def label_night(readings: list[Reading], forecast: list[ForecastPoint],
                params: RoomParams, night_of: date) -> str:
    """"heating_on_overnight", "pure_cooling" or "unknown" for the night of
    `night_of` (22:00-07:00). Checked every hour from 23:30; one "on" is enough.
    "pure_cooling" nights are the input to calibrate.fit_tau()."""
    bedtime, morning = NIGHT_WINDOW
    when = (datetime.combine(night_of, bedtime, tzinfo=LONDON)
            + timedelta(minutes=INFER_WINDOW_MIN))
    last = datetime.combine(night_of + timedelta(days=1), morning, tzinfo=LONDON)
    results = []
    while when <= last:
        results.append(infer_heating(readings, forecast, params, when).heating_on)
        when += timedelta(hours=1)
    if True in results:
        return "heating_on_overnight"
    if False in results:
        return "pure_cooling"
    return "unknown"


def heating_habit(labels: list[str]) -> tuple[int, int, str]:
    """(nights_on, nights_known, text), e.g. (2, 7, "heating was on 2 of the last 7 nights").
    Pass the last 7 nights' labels from label_night()."""
    known = [label for label in labels if label != "unknown"]
    on = known.count("heating_on_overnight")
    if not known:
        return 0, 0, "no heating history yet"
    return on, len(known), f"heating was on {on} of the last {len(known)} nights"
