import pytest
import random
from datetime import timedelta

from warmly.model.calibrate import fit_tau
from warmly.model.room_model import simulate
from warmly.model.types import Reading, RoomParams
from helpers import at, constant_forecast


def _cooling_readings(tau_h, hours=10, noise=0.08, seed=1):
    forecast = constant_forecast(-1.0, at(22, 16))
    start = at(22, 21)
    curve = simulate(18.5, start, start + timedelta(hours=hours),
                     RoomParams(tau_h, 60, 1.5, 300), [], forecast)
    rng = random.Random(seed)
    readings = [Reading("test", "bedroom", when, round(t + rng.gauss(0, noise), 1), "synthetic")
                for when, t, _ in curve]
    return readings, forecast


def test_recovers_tau():
    """Curve simulated with tau = 40 plus noise fits tau within ±15%."""
    readings, forecast = _cooling_readings(40)
    tau = fit_tau(readings, forecast)
    assert tau is not None
    assert 34 <= tau <= 46


def test_too_little_data():
    readings, forecast = _cooling_readings(40)
    assert fit_tau(readings[:8], forecast) is None  # under 3 hours


def test_not_cooling():
    forecast = constant_forecast(-1.0, at(22, 16))
    warming = [Reading("test", "living", at(22, 18, 0), 16.0, "synthetic"),
               Reading("test", "living", at(22, 20, 0), 17.0, "synthetic"),
               Reading("test", "living", at(22, 22, 0), 18.0, "synthetic")]
    assert fit_tau(warming, forecast) is None



def test_learn_tau():
    """Three heating-off nights from a room that really has tau 30 h, one
    heated night, and a preset guess of 45 h: learns about 30 h."""
    from datetime import date, time
    from warmly.model.calibrate import learn_tau, params_with_fitted_tau
    from warmly.model.types import HeatingWindow

    forecast = constant_forecast(-1.0, at(22, 16), hours=24 * 4)
    real = RoomParams(30, 45, 1.5, 300)
    rng = random.Random(3)
    nights = []
    for day in [22, 23, 24, 25]:
        heated = [HeatingWindow(time(21), time(7), 18.5)] if day == 25 else []
        curve = simulate(18.5, at(day, 21), at(day + 1, 7, 30), real, heated, forecast)
        readings = [Reading("t", "bedroom", when, round(t + rng.gauss(0, 0.08), 1), "synthetic")
                    for when, t, _ in curve]
        nights.append((date(2026, 11, day), readings))

    preset = RoomParams(45, 30, 1.5, 300)
    tau = learn_tau(nights, forecast, preset)
    assert 25.5 <= tau <= 34.5
    assert learn_tau(nights[:1], forecast, preset) == 45      # one night isn't enough

    fitted = params_with_fitted_tau(preset, tau)
    assert fitted.heat_loss_w_per_c * fitted.tau_h == pytest.approx(30 * 45)
