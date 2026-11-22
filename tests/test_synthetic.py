"""Design doc §6 acceptance checks."""
from datetime import date, timedelta

import pytest

from warmly.model.forecast import load_fallback
from warmly.model.synthetic import (build_demo_homes, demo_window, export_csv,
                                    generate_curves)
from warmly.sources import SimClock, SyntheticSource
from helpers import at, constant_forecast

NIGHT = date(2026, 11, 22)


def _curves(forecast=None):
    homes = build_demo_homes(NIGHT)
    return generate_curves(homes, forecast or load_fallback(NIGHT), *demo_window(NIGHT))


def _temp(readings, when):
    return next(r.temp_c for r in readings if r.timestamp == when)


def test_margaret_worked_example():
    """Part 1, constant -1°C forecast: 18.5°C at 17:00 -> 17.1 / 16.2 / 12.4°C."""
    living = _curves(constant_forecast(-1.0, at(22, 15), hours=24))[("margaret", "living")]
    assert _temp(living, at(22, 17)) == pytest.approx(18.5, abs=0.2)
    assert _temp(living, at(22, 20)) == pytest.approx(17.1, abs=0.2)
    assert _temp(living, at(22, 22)) == pytest.approx(16.2, abs=0.2)
    # The doc's "12.4°C at 08:00" assumes heating off until 08:00, but her
    # schedule turns it back on at 07:00 (test_margaret_regression checks the
    # 08:00 figure with the heating off). At 07:00: -1 + 19.5 e^(-14/40) = 12.7°C.
    assert _temp(living, at(23, 7)) == pytest.approx(12.7, abs=0.25)   # 12.74 + seeded noise


def test_acceptance():
    """Part 2, the fallback forecast (deterministic)."""
    curves = _curves()

    # Frank's ROOM IN USE never drops below 18°C: living room until 22:00,
    # bedroom 22:00-07:00. (His living room does fall below 18°C from about
    # 04:00 after its heating goes off at 22:30; nobody's in it.)
    for r in curves[("frank", "living")]:
        if at(22, 16) <= r.timestamp < at(22, 22):
            assert r.temp_c >= 18.0
    for r in curves[("frank", "bedroom")]:
        if at(22, 22) <= r.timestamp < at(23, 7):
            assert r.temp_c >= 18.0

    # Joan: still warm at 01:00, below 18°C by 04:00, about 16-17.5°C at 07:00
    joan = curves[("joan", "bedroom")]
    assert _temp(joan, at(23, 1)) >= 18.0
    assert _temp(joan, at(23, 4)) < 18.0
    assert 16.0 <= _temp(joan, at(23, 7)) <= 17.5

    # Eileen: about 18°C at 20:30 (heating off since 20:00, so 17.8), 11.5-12.8°C at 07:00
    eileen = curves[("eileen", "bedroom")]
    assert _temp(eileen, at(22, 20)) == pytest.approx(18.0, abs=0.2)
    assert 17.6 <= _temp(eileen, at(22, 20, 30)) <= 18.0
    assert 11.5 <= _temp(eileen, at(23, 7)) <= 12.8

    # Pat's living room is >= 18°C during 16:00-22:00
    for r in curves[("pat", "living")]:
        if at(22, 16) <= r.timestamp <= at(22, 22):
            assert r.temp_c >= 18.0


def test_ray_goes_offline():
    curves = _curves()
    for room in ["living", "bedroom"]:
        for r in curves[("ray", room)]:
            if r.timestamp >= at(22, 19):
                assert r.temp_c is None
            else:
                assert r.temp_c is not None


def test_repeatable_and_rounded():
    first, second = _curves(), _curves()
    assert first == second
    for readings in first.values():
        for r in readings:
            if r.temp_c is not None:
                assert round(r.temp_c, 1) == r.temp_c


def test_export_csv(tmp_path):
    curves = _curves()
    path = export_csv(curves, tmp_path / "readings.csv")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "home_id,room,timestamp,temp_c,source"
    assert len(lines) == 1 + sum(len(r) for r in curves.values())


def test_synthetic_source_follows_clock():
    homes = build_demo_homes(NIGHT)
    clock = SimClock(at(22, 16))
    clock.pause()
    source = SyntheticSource(homes, load_fallback(NIGHT), clock)

    clock.jump_to(at(22, 20, 5))
    reading = source.get_reading("margaret", "living")
    assert reading.timestamp == at(22, 20)
    history = source.get_history("margaret", "living", since=at(22, 19))
    assert [r.timestamp for r in history] == [at(22, 19), at(22, 19, 15), at(22, 19, 30),
                                              at(22, 19, 45), at(22, 20)]

    clock.jump_to(at(22, 15))
    assert source.get_reading("margaret", "living").temp_c is None


def test_sim_clock_speed(monkeypatch):
    import warmly.sources.base as base
    fake_now = [1000.0]
    monkeypatch.setattr(base.real_time, "monotonic", lambda: fake_now[0])

    clock = SimClock(at(22, 16), speed=720)
    fake_now[0] += 5  # 5 real seconds = 1 simulated hour
    assert clock.now() == at(22, 17)
    clock.pause()
    fake_now[0] += 5
    assert clock.now() == at(22, 17)
    clock.resume()
    fake_now[0] += 5
    assert clock.now() == at(22, 18)
    assert clock.now() - at(22, 16) == timedelta(hours=2)
