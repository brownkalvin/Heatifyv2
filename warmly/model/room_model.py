"""The physics: how a room cools down and warms up.

The underlying model (design doc §2.1) is a first-order "RC" model:

    dT_in/dt = (T_out - T_in) / tau  +  P_heat / C

- tau (hours) is the cooling time constant. Higher = better insulated.
- H (W/°C) is the room's heat-loss coefficient.
- C = H * tau * 3600 (J/°C) is the room's heat capacity.
- P_heat (W) is the heat delivered by the heating, so the heating term
  works out to P_heat / (H * tau) °C per hour.

This is Newton's law of cooling with k = 1/tau per hour (tau = 40 h gives
k = 0.025 /h). Its general solution is

    T(t) = T(0) e^(-kt) + k * integral from 0 to t of e^(-k(t-s)) T_out(s) ds

so the room "remembers" roughly the last tau hours of weather.

Cooling uses the exact solution over each step (stable for any step size):

    T_in(t + dt) = T_out + (T_in(t) - T_out) * exp(-dt / tau)

Outdoor temperature over a step (design doc §2.2b), `outdoor_mode`:
- "pulse" (default): T_out is held at its value at the step MIDPOINT. Each
  forecast value is a pulse; in Laplace terms the house is the low-pass
  filter k/(s+k), and the formula above is the exact response to a pulse.
  Taking the value at the START of the step instead lags about 30 minutes.
- "ramp": T_out changes linearly from T1 to T2 over the step, slope b.
  Exact solution:  T_end = T2 - b/k + (T_start - T1 + b/k) * exp(-k dt).
  With b = 0 it is the formula above. Kept as a cross-check: at 15-minute
  steps the two agree to well under 0.01°C.

Heating does NOT use the P_heat term. A single tau lumps air and walls
together, which makes warm-up far too slow (a 1.5 kW radiator would take
hours to add 1°C). Real air warms much faster than walls, so heating uses
a simple thermostat rule instead: warm at a fixed rate, then hold.
"""
import math
from datetime import datetime, time, timedelta
from typing import Literal

from .forecast import temp_at
from .types import ForecastPoint, HeatingWindow, RoomParams, check_events


OutdoorMode = Literal["pulse", "ramp"]


def cool(t_in: float, t_out: float, tau_h: float, dt_h: float,
         t_out_end: float | None = None) -> float:
    """Exact cooling (or warming towards a warmer outside) with no heating.
    If `t_out_end` is given, outside ramps linearly from t_out to t_out_end."""
    if t_out_end is None:
        return t_out + (t_in - t_out) * math.exp(-dt_h / tau_h)
    k = 1 / tau_h
    b = (t_out_end - t_out) / dt_h              # °C per hour
    return t_out_end - b / k + (t_in - t_out + b / k) * math.exp(-k * dt_h)


def step(t_in: float, t_out: float, params: RoomParams,
         dt_h: float, heating_on: bool, setpoint_c: float | None,
         outdoor_mode: OutdoorMode = "pulse",
         t_out_end: float | None = None) -> tuple[float, float]:
    """Advance one step. Returns (new_t_in, energy_used_wh).

    outdoor_mode "pulse": `t_out` is the outdoor temperature at the step's
    midpoint. "ramp": `t_out` is at the start and `t_out_end` at the end.

    - Heating off: exact cooling, no energy.
    - Warming up (below setpoint): rise at warmup_rate_c_per_h, stop at the
      setpoint. Energy = losses to outside + energy to warm the room.
    - At or above setpoint: the thermostat holds the setpoint. Energy = the
      losses H * (T_set - T_out) * dt. If the room is warmer than the
      setpoint it cools naturally first and the boiler stays idle.
    """
    if outdoor_mode == "ramp":
        if t_out_end is None:
            raise ValueError('outdoor_mode="ramp" needs t_out_end')
        ramp_end, t_out_avg = t_out_end, (t_out + t_out_end) / 2
    elif outdoor_mode == "pulse":
        ramp_end, t_out_avg = None, t_out
    else:
        raise ValueError(f"outdoor_mode must be 'pulse' or 'ramp', got {outdoor_mode!r}")

    if not heating_on or setpoint_c is None:
        return cool(t_in, t_out, params.tau_h, dt_h, ramp_end), 0.0

    if t_in < setpoint_c:
        new_t_in = min(setpoint_c, t_in + params.warmup_rate_c_per_h * dt_h)
        losses_wh = max(0.0, params.heat_loss_w_per_c * (t_in - t_out_avg) * dt_h)
        warmup_wh = params.warmup_wh_per_c * (new_t_in - t_in)
        return new_t_in, losses_wh + warmup_wh

    cooled = cool(t_in, t_out, params.tau_h, dt_h, ramp_end)
    if cooled >= setpoint_c:
        return cooled, 0.0
    hold_wh = max(0.0, params.heat_loss_w_per_c * (setpoint_c - t_out_avg) * dt_h)
    return setpoint_c, hold_wh


def _in_window(t: time, window: HeatingWindow) -> bool:
    """Is clock time `t` inside the window? Handles windows past midnight."""
    if window.start <= window.end:
        return window.start <= t < window.end
    # Wraps midnight, e.g. 21:00 to 07:00
    return t >= window.start or t < window.end


def is_heating_on(when: datetime, schedule: list[HeatingWindow]) -> tuple[bool, float | None]:
    """Is the heating scheduled on at `when`? Returns (on, setpoint).

    Handles windows that wrap past midnight. If windows overlap, the
    highest setpoint wins.
    """
    setpoints = [w.setpoint_c for w in schedule if _in_window(when.time(), w)]
    if not setpoints:
        return False, None
    return True, max(setpoints)


def heating_blocked(when: datetime, events: list[dict]) -> bool:
    """True if an event stops the heating at `when`.

    - {"type": "boiler_failure", "at": datetime}: off from then on.
    - {"type": "heating_override_off", "from": datetime, "to": datetime}:
      the person switched it off for that period.
    - {"type": "sensor_offline", ...}: ignored here (the physics carries on).
    Any other type raises ValueError.
    """
    check_events(events)
    for event in events:
        if event["type"] == "boiler_failure" and when >= event["at"]:
            return True
        if event["type"] == "heating_override_off" and event["from"] <= when < event["to"]:
            return True
    return False


def simulate(t_start: float, start: datetime, end: datetime, params: RoomParams,
             schedule: list[HeatingWindow], forecast: list[ForecastPoint],
             dt_min: int = 15, events: list[dict] | None = None,
             outdoor_mode: OutdoorMode = "pulse") -> list[tuple[datetime, float, float]]:
    """Returns [(timestamp, t_in, energy_wh_this_step), ...].

    The first row is the start (energy 0). Each later row is the end of a
    step and the energy used during that step. The heating decision is made
    at the start of each step. Outdoor temperature: see `outdoor_mode` in
    the module docstring ("pulse" uses the step midpoint).
    """
    events = events or []
    dt = timedelta(minutes=dt_min)
    dt_h = dt_min / 60

    results = [(start, t_start, 0.0)]
    t_in = t_start
    when = start
    while when + dt <= end:
        heating_on, setpoint = is_heating_on(when, schedule)
        if heating_blocked(when, events):
            heating_on, setpoint = False, None
        if outdoor_mode == "ramp":
            t_in, energy_wh = step(t_in, temp_at(forecast, when), params, dt_h, heating_on,
                                   setpoint, "ramp", t_out_end=temp_at(forecast, when + dt))
        else:
            t_in, energy_wh = step(t_in, temp_at(forecast, when + dt / 2), params, dt_h,
                                   heating_on, setpoint)
        when = when + dt
        results.append((when, t_in, energy_wh))
    return results
