from datetime import date, time, timedelta

from warmly.model.forecast import load_fallback
from warmly.model.homes import build_home
from warmly.model.predict import (daytime_status, predict_if, predict_now, predict_overnight,
                                  predict_week, predict_without_reading, summarise_night)
from warmly.model.room_model import simulate
from warmly.model.synthetic import build_demo_homes, demo_window, generate_curves
from warmly.model.types import HeatingWindow, Reading, RoomParams
from helpers import at, constant_forecast

NIGHT = date(2026, 11, 22)
WINDOW_AJAR = {"bedroom": {"tau_h": 25, "heat_loss_w_per_c": 50}}


def _eileen_like(schedule=None):
    return build_home("eileen", "Eileen", 88, "Barton", "flat", sap_score=72,
                      schedule=schedule, room_overrides=WINDOW_AJAR)


def _readings(start, temps, step_min=15, room="living"):
    return [Reading("test", room, start + timedelta(minutes=step_min * i), t, "synthetic")
            for i, t in enumerate(temps)]


def _demo_curves():
    homes = {h.home_id: h for h in build_demo_homes(NIGHT)}
    forecast = load_fallback(NIGHT)
    return homes, forecast, generate_curves(list(homes.values()), forecast, *demo_window(NIGHT))


def test_overnight_breach():
    """Eileen-style bedroom at -2°C outside: below 18°C, minimum about 11-12.5°C."""
    forecast = constant_forecast(-2.0, at(22, 18))
    p = predict_overnight(_eileen_like(), "bedroom", at(22, 20, 30), 18.5, forecast)
    assert p.will_breach_18 and p.scenario == "off"
    assert 11.0 <= p.predicted_min_c <= 12.5
    assert p.predicted_min_at == at(23, 7)
    assert p.predicted_at_7am_c == p.predicted_min_c
    assert p.hours_below_18 == 9  # 22:00 to 07:00, all below 18°C


def test_scenarios_ignore_schedule():
    """Live scenarios never read the home's (synthetic) schedule."""
    forecast = constant_forecast(-2.0, at(22, 18))
    plain = _eileen_like()
    heated = _eileen_like({"bedroom": [HeatingWindow(time(16), time(8), 20.0)]})
    for scenario in ["off", "hold_target_overnight", "warm_1h_before_bed"]:
        a = predict_if(plain, "bedroom", at(22, 20, 30), 18.5, forecast, scenario)
        b = predict_if(heated, "bedroom", at(22, 20, 30), 18.5, forecast, scenario)
        assert a.curve == b.curve, scenario
    # ...only the demo-only "as_scheduled" does
    a = predict_if(plain, "bedroom", at(22, 20, 30), 18.5, forecast, "as_scheduled")
    b = predict_if(heated, "bedroom", at(22, 20, 30), 18.5, forecast, "as_scheduled")
    assert a.curve != b.curve


def test_scenarios():
    forecast = constant_forecast(-2.0, at(22, 18))
    home = _eileen_like()

    def run(scenario):
        return predict_if(home, "bedroom", at(22, 20, 30), 18.5, forecast, scenario)

    off, hold, warm = run("off"), run("hold_target_overnight"), run("warm_1h_before_bed")
    assert hold.predicted_at_7am_c == 18.0
    assert min(t for _, t in hold.curve) == 18.0          # never below 18°C
    assert dict(warm.curve)[at(22, 22)] == 19.0           # 19°C at bedtime, then off
    assert off.predicted_at_7am_c < warm.predicted_at_7am_c < hold.predicted_at_7am_c


def test_future_events_are_not_known():
    """A 20:30 prediction can't know the boiler will fail at 01:00."""
    home = _eileen_like({"bedroom": [HeatingWindow(time(21), time(7), 18.5)]})
    home.events = [{"type": "boiler_failure", "at": at(23, 1)}]
    forecast = constant_forecast(-2.0, at(22, 18))
    p = predict_if(home, "bedroom", at(22, 20, 30), 18.5, forecast, "as_scheduled")
    assert p.predicted_at_7am_c == 18.5


def test_reanchor():
    """Joan: restarting the heating-off curve from each new reading makes the
    morning figure track the real room. (The doc's wording, "the 02:00
    prediction is lower than the 20:30 one", can't hold for a heating-off
    prediction: at 20:30 the off-case is already the worst case. See notes.)"""
    homes, forecast, curves = _demo_curves()
    joan, bedroom = homes["joan"], curves[("joan", "bedroom")]
    actual_7am = next(r.temp_c for r in bedroom if r.timestamp == at(23, 7))

    evening = predict_now(joan, "bedroom", bedroom, at(22, 20, 30), forecast)
    night = predict_now(joan, "bedroom", bedroom, at(23, 2), forecast)
    assert night.curve[0][0] == at(23, 2)                     # restarted from the 02:00 reading
    assert abs(night.predicted_at_7am_c - actual_7am) < 0.3   # and now matches the morning
    assert abs(evening.predicted_at_7am_c - actual_7am) > 0.7


def test_no_reading_fallback():
    """Ray: all readings None -> estimate from 18°C, marked assumed_start, no crash."""
    homes, forecast, curves = _demo_curves()
    ray, bedroom = homes["ray"], curves[("ray", "bedroom")]
    offline = [r for r in bedroom if r.timestamp >= at(22, 19)]
    assert all(r.temp_c is None for r in offline)

    p = predict_without_reading(ray, "bedroom", at(22, 21, 30), forecast, offline)
    assert p.assumed_start and p.curve[0] == (at(22, 21, 30), 18.0)
    assert predict_without_reading(ray, "bedroom", at(22, 21, 30), forecast).assumed_start

    # At 20:30 the 18:45 reading is under 2 hours old, so it's used instead
    p = predict_without_reading(ray, "bedroom", at(22, 20, 30), forecast, bedroom)
    assert not p.assumed_start and p.curve[0][0] == at(22, 18, 45)


def test_overnight_cold_does_not_trigger_morning_check_in():
    """Cold all night, heating comes on at 07:00: no check-in at 07:00."""
    overnight = _readings(at(23, 1), [16.0] * 25)  # 01:00 to 07:00, flat
    assert not daytime_status(overnight, at(23, 7)).should_check_in
    still_cold = _readings(at(23, 1), [16.0] * 33)  # ...and still flat at 09:00
    assert daytime_status(still_cold, at(23, 9)).should_check_in


def test_daytime_not_warming():
    # Cold and flat for 2.5 hours -> check in
    flat = _readings(at(22, 16), [17.0] * 11)
    status = daytime_status(flat, at(22, 18, 30))
    assert status.below_18_for_h >= 2
    assert not status.warming
    assert status.should_check_in

    # Cold but rising 0.5°C/h -> don't check in
    rising = _readings(at(22, 16), [15.0 + 0.125 * i for i in range(11)])
    status = daytime_status(rising, at(22, 18, 30))
    assert status.warming
    assert not status.should_check_in

    # Cold and flat but after 22:00 -> don't check in (that's the night check)
    late = _readings(at(22, 20), [17.0] * 11)
    assert not daytime_status(late, at(22, 22, 30)).should_check_in


def test_daytime_heating_on():
    params = RoomParams(50, 50, 2.0, 300)
    forecast = constant_forecast(3.0, at(22, 6))
    warming = [Reading("t", "living", when, round(t, 1), "synthetic") for when, t, _ in
               simulate(14.0, at(23, 7), at(23, 9), params,
                        [HeatingWindow(time(7), time(22), 20.0)], forecast)]
    assert daytime_status(warming, at(23, 9), forecast, params).heating_on is True
    assert daytime_status(warming, at(23, 9)).heating_on is None   # not asked


def test_ignores_none():
    temps = [17.0, None, None, 16.5, None, None, None, 16.0]
    readings = _readings(at(22, 22), temps, step_min=60, room="bedroom")
    summary = summarise_night(readings, NIGHT)
    assert summary.min_c == 16.0
    assert summary.min_at == at(23, 5)
    assert summary.missing_fraction == 5 / 8
    assert summary.no_reading

    status = daytime_status(_readings(at(22, 16), [17.0, None, None, 17.0]), at(22, 16, 45))
    assert status.below_18_for_h == 0.75

    # Everything missing: no crash
    all_missing = summarise_night(_readings(at(22, 22), [None] * 4), NIGHT)
    assert all_missing.min_c is None and all_missing.missing_fraction == 1.0


def test_night_summary():
    temps = [18.5, 18.2, 17.9, 17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.5]  # 22:00–07:00
    summary = summarise_night(_readings(at(22, 22), temps, step_min=60), NIGHT)
    assert summary.min_c == 11.5 and summary.night_of == NIGHT
    assert summary.cold_from == at(23, 0)
    assert summary.cold_to == at(23, 7)
    assert summary.hours_below_18 == 7
    assert not summary.breached_danger_floor


def test_week():
    home = _eileen_like()
    forecast = constant_forecast(-1.0, at(22, 16), hours=24 * 8)
    week = predict_week(home, "bedroom", at(22, 20, 30), 17.0, forecast)

    assert [n.days_ahead for n in week] == [1, 2, 3, 4, 5, 6, 7]
    assert [n.confidence for n in week] == ["likely"] * 3 + ["possible"] * 4
    width = [n.range_c[1] - n.range_c[0] for n in week]
    assert min(width[3:]) > max(width[:3])
    for n in week:
        assert n.range_c[0] < n.predicted_min_c < n.range_c[1]
        assert n.freezing and not n.snow
    assert week[0].predicted_min_c < week[1].predicted_min_c   # tonight starts at 17°C, not 18

    # Not within 2 hours of bedtime: tonight starts from 18°C at 22:00 too
    afternoon = predict_week(home, "bedroom", at(22, 16), 15.0, forecast)
    assert afternoon[0].predicted_min_c == afternoon[1].predicted_min_c

    # A short forecast gives fewer nights instead of made-up ones
    short = constant_forecast(-1.0, at(22, 16), hours=48)
    assert len(predict_week(home, "bedroom", at(22, 20, 30), 17.0, short)) == 2


def test_crossing():
    """Fallback forecast at 17:00: Margaret's living room is predicted to
    cross 18°C between 18:30 and 19:00; Frank's won't within 6 hours."""
    from warmly.model.predict import predict_crossing
    homes, forecast, curves = _demo_curves()

    def crossing(home_id):
        t_now = next(r.temp_c for r in curves[(home_id, "living")] if r.timestamp == at(22, 17))
        return predict_crossing(homes[home_id], "living", at(22, 17), t_now, forecast)

    assert at(22, 18, 30) <= crossing("margaret") <= at(22, 19)
    assert crossing("frank") is None
    assert predict_crossing(homes["margaret"], "living", at(22, 21), 17.0, forecast) is None
