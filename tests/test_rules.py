from datetime import date, timedelta

from warmly.model.rules import (bedtime_message_due, daytime_check_due, morning_check_due,
                                night_alert_due, one_conversation_per_evening,
                                sensor_offline_due, status_colour, weekly_summary)
from warmly.model.types import DaytimeStatus, NightSummary, OvernightPrediction, Reading
from helpers import at


def _pred(breach: bool) -> OvernightPrediction:
    low = 12.0 if breach else 18.2
    return OvernightPrediction("x", "bedroom", low, at(23, 7), low, 9.0 if breach else 0.0,
                               breach, [])


def _summary(hours_below: float, min_c: float = 15.0, day: int = 1) -> NightSummary:
    return NightSummary(date(2026, 11, day), min_c, at(23, 5), None, None, hours_below,
                        False, 0.0)


def test_bedtime_backoff():
    assert bedtime_message_due(_pred(True), 0, snow=False, freezing=False)
    assert not bedtime_message_due(_pred(True), 3, snow=False, freezing=False)   # mild night
    assert bedtime_message_due(_pred(True), 3, snow=False, freezing=True)
    assert bedtime_message_due(_pred(True), 5, snow=True, freezing=False)
    assert bedtime_message_due(_pred(True), 4, False, False, cold_health_alert=True)
    assert not bedtime_message_due(_pred(False), 0, snow=True, freezing=True)    # stays warm


def test_night_never_messages_person():
    def reading(day_hour, temp):
        return Reading("x", "bedroom", at(*day_hour), temp, "synthetic")

    assert night_alert_due(reading((23, 3), 9.5), opted_in=True)
    assert not night_alert_due(reading((23, 3), 10.5), opted_in=True)    # above the floor
    assert not night_alert_due(reading((23, 3), 9.5), opted_in=False)    # not opted in
    assert not night_alert_due(reading((23, 3), None), opted_in=True)    # no reading
    assert not night_alert_due(reading((22, 15), 9.5), opted_in=True)    # daytime


def test_one_conversation():
    """Margaret: daytime check-in already sent -> no separate bedtime message."""
    breach = _pred(True)
    separate = (bedtime_message_due(breach, 0, False, True)
                and one_conversation_per_evening(already_messaged_since_17=True))
    assert not separate
    assert one_conversation_per_evening(already_messaged_since_17=False)


def test_morning_and_daytime():
    assert morning_check_due(_summary(2.0))
    assert not morning_check_due(_summary(1.75))
    assert daytime_check_due(DaytimeStatus(2.5, False, None, True))
    assert not daytime_check_due(DaytimeStatus(2.5, True, True, False))


def test_weekly_text_consistent():
    week = [_summary(h, min_c=m, day=d) for d, (h, m) in
            enumerate([(9, 9.0), (3, 14.0), (2, 15.5), (1, 16.0), (9, 11.6),
                       (0.5, 17.0), (4, 13.0), (2.5, 15.0)], start=1)]
    summary = weekly_summary(week)   # 8 nights given: only the last 7 count
    assert summary.cold_nights == 5  # 3, 2, 9, 4, 2.5 hours
    assert summary.text == "Bedroom below 18°C for 2+ hours on 5 of 7 nights (lowest 11.6°C)"
    assert len(summary.nights) == 7 and summary.nights[0][0] == date(2026, 11, 2)


def test_status_colour():
    assert status_colour(18.0) == "green"
    assert status_colour(15.0) == "amber"
    assert status_colour(12.9) == "red"
    assert status_colour(None) == "grey"


def test_sensor_offline_due():
    assert not sensor_offline_due(at(22, 18, 45), at(22, 20, 30))
    assert sensor_offline_due(at(22, 18, 45), at(22, 20, 45))     # 2 hours of silence
    assert sensor_offline_due(None, at(22, 19))


def test_bedtime_rules_2_to_4():
    breach = _pred(True)
    assert not bedtime_message_due(breach, 0, False, True, bedroom_heating_on=True)
    assert bedtime_message_due(breach, 0, False, True, bedroom_heating_on=None)   # can't tell
    # Usually heated at night (5 of 7): skipped, unless snow or a cold-health alert
    assert not bedtime_message_due(breach, 0, False, True, habit_on_nights=5)
    assert bedtime_message_due(breach, 0, True, True, habit_on_nights=5)
    assert bedtime_message_due(breach, 0, False, False, True, habit_on_nights=6)
    assert bedtime_message_due(breach, 0, False, True, habit_on_nights=4)
    # Living room already cold: the daytime check-in carries it instead
    assert not bedtime_message_due(breach, 0, False, True, living_below_18=True)


def test_bedtime_skips_heated_homes():
    """Fallback night. Frank and Joan (bedroom heating on) get no bedtime
    message; Margaret's goes into her daytime check-in; Eileen gets one once
    her heating (off at 20:00) has been off long enough to see, by 21:00."""
    from datetime import date
    from warmly.model.forecast import freezing_tonight, load_fallback, snow_tonight
    from warmly.model.infer_heating import infer_heating
    from warmly.model.predict import predict_now
    from warmly.model.synthetic import build_demo_homes, demo_window, generate_curves

    night = date(2026, 11, 22)
    homes = {h.home_id: h for h in build_demo_homes(night)}
    forecast = load_fallback(night)
    curves = generate_curves(list(homes.values()), forecast, *demo_window(night))
    def due(home_id, now=at(22, 20, 30)):
        home, bedroom = homes[home_id], curves[(home_id, "bedroom")]
        living_now = next(r.temp_c for r in curves[(home_id, "living")] if r.timestamp == now)
        heating = infer_heating(bedroom, forecast, home.rooms["bedroom"], now)
        return bedtime_message_due(
            predict_now(home, "bedroom", bedroom, now, forecast), 0,
            snow_tonight(forecast, now), freezing_tonight(forecast, now),
            bedroom_heating_on=heating.heating_on, living_below_18=living_now < 18.0)

    assert not due("frank")
    assert not due("joan")
    assert not due("margaret")      # living room already cold: folded into the check-in
    assert due("eileen", at(22, 21))
    assert not due("frank", at(22, 21)) and not due("joan", at(22, 21))
