import random
from datetime import date, time

import pytest

from warmly.model.forecast import load_fallback
from warmly.model.infer_heating import heating_habit, infer_heating, label_night
from warmly.model.room_model import simulate
from warmly.model.synthetic import build_demo_homes, demo_window, generate_curves
from warmly.model.types import HeatingWindow, Reading, RoomParams
from helpers import at, constant_forecast

NIGHT = date(2026, 11, 22)
PARAMS = RoomParams(45, 40, 2.0, 300)


def _noisy(curve, seed):
    """simulate() rows -> readings with sensor noise and 0.1°C rounding."""
    rng = random.Random(seed)
    return [Reading("t", "bedroom", when, round(t + rng.gauss(0, 0.08), 1), "synthetic")
            for when, t, _ in curve]


def _demo():
    homes = {h.home_id: h for h in build_demo_homes(NIGHT)}
    forecast = load_fallback(NIGHT)
    return homes, forecast, generate_curves(list(homes.values()), forecast, *demo_window(NIGHT))


def test_detects_on_off():
    """Warming up -> on; fewer than 6 readings in 90 minutes -> None;
    a mild night where |expected| < 2·se -> None."""
    forecast = constant_forecast(0.0, at(22, 16))
    warming = simulate(15.0, at(22, 20), at(22, 22), PARAMS,
                       [HeatingWindow(time(20), time(23), 20.0)], forecast)
    result = infer_heating(_noisy(warming, 1), forecast, PARAMS, at(22, 22))
    assert result.heating_on is True
    assert result.est_power_w > 0

    cooling = simulate(18.5, at(22, 20), at(22, 23), PARAMS, [], forecast)
    assert infer_heating(_noisy(cooling, 1), forecast, PARAMS, at(22, 23)).heating_on is False
    few = _noisy(cooling, 1)[-5:]   # 5 readings
    assert infer_heating(few, forecast, PARAMS, at(22, 23)).heating_on is None

    mild = constant_forecast(17.0, at(22, 16))
    near_outside = simulate(18.0, at(22, 20), at(22, 23), PARAMS, [], mild)
    assert infer_heating(_noisy(near_outside, 1), mild, PARAMS, at(22, 23)).heating_on is None


def test_detects_held_steady():
    """Joan's bedroom (flat, tau 60 h) held at 18.5°C with 3°C outside -> on
    in >= 90% of 500 noisy windows; pure cooling -> on in <= 3%."""
    flat = RoomParams(60, 30, 2.5, 200)
    forecast = constant_forecast(3.0, at(22, 16))
    held = simulate(18.5, at(22, 19), at(22, 20, 30), flat,
                    [HeatingWindow(time(16), time(7), 18.5)], forecast)
    cooling = simulate(18.5, at(22, 19), at(22, 20, 30), flat, [], forecast)

    def share_on(curve):
        calls = [infer_heating(_noisy(curve, seed), forecast, flat, at(22, 20, 30)).heating_on
                 for seed in range(500)]
        return calls.count(True) / 500

    assert share_on(held) >= 0.90
    assert share_on(cooling) <= 0.03


def test_demo_homes():
    homes, forecast, curves = _demo()

    def on(home_id, room, when):
        return infer_heating(curves[(home_id, room)], forecast,
                             homes[home_id].rooms[room], when).heating_on

    assert on("margaret", "living", at(22, 20)) is False   # heating off since 17:00
    assert on("frank", "bedroom", at(22, 20, 30)) is True  # held at 18.5°C
    assert on("joan", "bedroom", at(22, 20, 30)) is True   # held at 18.5°C (a flat)
    assert on("joan", "bedroom", at(23, 3)) is False       # boiler failed at 01:00
    assert on("ray", "living", at(22, 21)) is None         # sensor offline


def test_label_night_and_habit():
    homes, forecast, curves = _demo()

    def label(home_id):
        return label_night(curves[(home_id, "bedroom")], forecast,
                           homes[home_id].rooms["bedroom"], NIGHT)

    assert label("margaret") == "pure_cooling"
    assert label("frank") == "heating_on_overnight"
    assert label("joan") == "heating_on_overnight"   # until the boiler failed
    assert label("ray") == "unknown"
    labels = ["heating_on_overnight"] * 2 + ["pure_cooling"] * 5
    assert heating_habit(labels) == (2, 7, "heating was on 2 of the last 7 nights")
    assert heating_habit(["unknown"]) == (0, 0, "no heating history yet")
