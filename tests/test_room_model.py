import pytest

from warmly.model.forecast import load_fallback
from warmly.model.room_model import simulate, step
from warmly.model.types import HeatingWindow, RoomParams
from helpers import at, constant_forecast

from datetime import date, time


def test_margaret_regression():
    """Design doc §2.4: tau 40 h, -1°C outside, 18.5°C at 17:00, heating off."""
    params = RoomParams(40, 60, 1.5, 300)
    forecast = constant_forecast(-1.0, at(22, 16))
    curve = simulate(18.5, at(22, 17), at(23, 8), params, [], forecast)
    temps = {when: t for when, t, _ in curve}

    assert temps[at(22, 20)] == pytest.approx(17.1, abs=0.1)
    assert temps[at(22, 22)] == pytest.approx(16.2, abs=0.1)
    assert temps[at(23, 8)] == pytest.approx(12.4, abs=0.1)
    assert all(energy == 0 for _, _, energy in curve)


def test_no_overshoot():
    params = RoomParams(40, 60, 2.0, 300)
    schedule = [HeatingWindow(time(16), time(23), 18.0)]
    forecast = constant_forecast(2.0, at(22, 16))
    curve = simulate(12.0, at(22, 16), at(22, 23), params, schedule, forecast)
    assert max(t for _, t, _ in curve) <= 18.0


def test_warmup_rate():
    """From 16°C, setpoint 18°C, 2°C/h: reaches 18°C after 1 hour (±1 step)."""
    params = RoomParams(40, 60, 2.0, 300)
    schedule = [HeatingWindow(time(0), time(23), 18.0)]
    forecast = constant_forecast(2.0, at(22, 10))
    curve = simulate(16.0, at(22, 12), at(22, 15), params, schedule, forecast)
    reached = next(when for when, t, _ in curve if t >= 18.0)
    minutes = (reached - at(22, 12)).total_seconds() / 60
    assert 45 <= minutes <= 75


def test_holds_setpoint():
    """At the setpoint the temperature stays flat and energy = H × ΔT × Δt."""
    params = RoomParams(40, 60, 2.0, 300)
    new_t, energy_wh = step(18.0, 3.0, params, dt_h=0.25, heating_on=True, setpoint_c=18.0)
    assert new_t == 18.0
    assert energy_wh == pytest.approx(60 * (18.0 - 3.0) * 0.25)


def test_step_size_independent():
    """15-minute and 60-minute steps agree within 0.05°C, on a changing night."""
    params = RoomParams(40, 60, 2.0, 300)
    forecast = load_fallback(date(2026, 11, 22))
    fine = simulate(18.5, at(22, 16), at(23, 10), params, [], forecast, dt_min=15)
    coarse = simulate(18.5, at(22, 16), at(23, 10), params, [], forecast, dt_min=60)
    fine_by_time = {when: t for when, t, _ in fine}
    for when, t, _ in coarse:
        assert fine_by_time[when] == pytest.approx(t, abs=0.05)


# --- Design doc §2.2b: pulse vs ramp, and the Laplace closed form ---------- #
import math

from warmly.model.room_model import cool

LAPLACE = dict(a=5.0, b=-0.1, c=0.002, amp=4.0, phi0=1.0, tau=40.0, t0=18.0)
OMEGA = 2 * math.pi / 24


def _t_out(t):
    p = LAPLACE
    return p["a"] + p["b"] * t + p["c"] * t * t + p["amp"] * math.cos(OMEGA * t - p["phi0"])


def _analytic(t):
    """T_in(t) = alpha + beta t + c t^2 + A/sqrt(1+(w/k)^2) cos(wt - phi0 - phi) + K e^(-kt)."""
    p = LAPLACE
    k = 1 / p["tau"]
    beta = p["b"] - 2 * p["c"] / k
    alpha = p["a"] - p["b"] / k + 2 * p["c"] / k ** 2
    gain = p["amp"] / math.sqrt(1 + (OMEGA / k) ** 2)
    phi = math.atan(OMEGA / k)

    def steady(s):
        return alpha + beta * s + p["c"] * s * s + gain * math.cos(OMEGA * s - p["phi0"] - phi)

    return steady(t) + (p["t0"] - steady(0)) * math.exp(-k * t)


def _pulse_error(dt_h, where):
    """Largest gap from the closed form over a week, stepping with pulses taken
    at the step's start (where=0) or midpoint (where=0.5)."""
    params = RoomParams(LAPLACE["tau"], 60, 2.0, 300)
    t_in, worst = LAPLACE["t0"], 0.0
    for i in range(round(168 / dt_h)):
        t_in, _ = step(t_in, _t_out((i + where) * dt_h), params, dt_h, False, None)
        worst = max(worst, abs(t_in - _analytic((i + 1) * dt_h)))
    return worst


def test_ramp_exact():
    """Linear ramp 4°C -> -2°C: one 3-hour ramp step = 180 one-minute steps."""
    params = RoomParams(40, 60, 2.0, 300)
    one_step, _ = step(18.0, 4.0, params, 3.0, False, None, "ramp", t_out_end=-2.0)
    t_in = 18.0
    for i in range(180):
        t_in, _ = step(t_in, 4.0 - 6.0 * (i + 0.5) / 180, params, 1 / 60, False, None)
    assert one_step == pytest.approx(t_in, abs=0.01)
    # A flat ramp is the plain cooling formula
    flat, _ = step(18.0, 3.0, params, 3.0, False, None, "ramp", t_out_end=3.0)
    assert flat == pytest.approx(cool(18.0, 3.0, 40, 3.0))


def test_ramp_matches_pulse_at_15_min():
    params = RoomParams(40, 60, 2.0, 300)
    forecast = load_fallback(date(2026, 11, 22))
    pulse = simulate(18.5, at(22, 16), at(23, 10), params, [], forecast)
    ramp = simulate(18.5, at(22, 16), at(23, 10), params, [], forecast, outdoor_mode="ramp")
    for (_, a, _), (_, b, _) in zip(pulse, ramp):
        assert a == pytest.approx(b, abs=0.01)
    # The Margaret regression (constant outside) is the same either way
    flat = constant_forecast(-1.0, at(22, 16))
    curve = simulate(18.5, at(22, 17), at(23, 8), RoomParams(40, 60, 1.5, 300), [], flat,
                     outdoor_mode="ramp")
    assert curve[-1][1] == pytest.approx(12.4, abs=0.1)


def test_laplace_analytic():
    """15-minute midpoint pulses match the closed form over a week."""
    assert _pulse_error(0.25, 0.5) < 0.05
    # Slide numbers: at tau = 40 h the house damps the daily swing ~10x, delays it ~5.6 h
    k = 1 / 40
    assert 1 / math.sqrt(1 + (OMEGA / k) ** 2) == pytest.approx(1 / 10.5, rel=0.05)
    assert math.atan(OMEGA / k) / OMEGA == pytest.approx(5.6, abs=0.1)


def test_pulse_midpoint():
    """Hourly pulses from the start of the hour lag; midpoint pulses don't."""
    assert _pulse_error(1.0, 0.0) > 0.1
    assert _pulse_error(1.0, 0.5) < 0.05
