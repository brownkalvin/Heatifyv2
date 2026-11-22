"""Pure decision helpers for the trigger engine (design doc §4.7).

This package DECIDES; the backend ACTS. These functions send nothing and
store nothing: everything they need, including the time, is passed in.
The backend keeps state such as declines, opt-ins, who was messaged when,
and which alerts have already gone out.
"""
from datetime import datetime, timedelta

from .homes import (AMBER_FROM_C, DANGER_FLOOR_C, DAYTIME_WINDOW, DECLINES_BEFORE_BACKOFF,
                    MORNING_CHECK_AFTER_H, NIGHT_WINDOW, SENSOR_OFFLINE_ALERT_MIN, TARGET_C,
                    USUALLY_ON_NIGHTS)
from .types import (DaytimeStatus, NightSummary, OvernightPrediction, Reading, Room,
                    WeeklySummary)


def bedtime_message_due(pred_off: OvernightPrediction, declines_recent: int,
                        snow: bool, freezing: bool, cold_health_alert: bool = False,
                        bedroom_heating_on: bool | None = None, habit_on_nights: int = 0,
                        living_below_18: bool = False) -> bool:
    """Should tonight's bedtime message go out? Only if ALL of these hold:

    1. the heating-off case drops below 18°C;
    2. the bedroom heating isn't clearly on now (infer_heating isn't True);
    3. the heating isn't usually on at night (on for fewer than 5 of the
       last 7 known nights), unless there's snow or a cold-health alert;
    4. the living room isn't already below 18°C: if it is, the daytime
       check-in owns tonight's conversation and carries the prediction;
    5. after DECLINES_BEFORE_BACKOFF recent declines, only on the coldest
       nights (snow, freezing, or a UKHSA cold-health alert).

    Rules 2 and 3 stop homes whose heating is on (Frank, Joan) getting a
    message every cold night.
    """
    if not pred_off.will_breach_18:
        return False
    if bedroom_heating_on is True:
        return False
    if habit_on_nights >= USUALLY_ON_NIGHTS and not (snow or cold_health_alert):
        return False
    if living_below_18:
        return False
    if declines_recent >= DECLINES_BEFORE_BACKOFF:
        return snow or freezing or cold_health_alert
    return True


def _is_night(when: datetime) -> bool:
    bedtime, morning = NIGHT_WINDOW
    return when.time() >= bedtime or when.time() < morning


def night_alert_due(reading: Reading, opted_in: bool) -> bool:
    """Should the FAMILY be alerted about this overnight reading?

    Between 22:00 and 07:00 Warmly never messages the person themselves.
    The family is alerted only if they opted in and the room is below
    DANGER_FLOOR_C. A missing reading isn't an alert here; see
    sensor_offline_due().
    """
    if not _is_night(reading.timestamp) or reading.temp_c is None:
        return False
    return opted_in and reading.temp_c < DANGER_FLOOR_C


def morning_check_due(summary: NightSummary) -> bool:
    """Morning check-in: the bedroom was below 18°C for at least 2 hours overnight."""
    return summary.hours_below_18 >= MORNING_CHECK_AFTER_H


def daytime_check_due(status: DaytimeStatus) -> bool:
    """Thin wrapper, so every "is a message due" decision lives in this file."""
    return status.should_check_in


def one_conversation_per_evening(already_messaged_since_17: bool) -> bool:
    """May we start a NEW conversation with the person this evening?

    False if they were already messaged since 17:00 (the bedtime message,
    or a daytime check-in and its follow-up). Then the second message isn't
    sent on its own: its content goes into the existing conversation.
    """
    return not already_messaged_since_17


def sensor_offline_due(last_seen: datetime | None, now: datetime) -> bool:
    """Has this room's thermometer been silent long enough to tell the family?

    True once there has been no real reading for SENSOR_OFFLINE_ALERT_MIN
    (or ever). The BACKEND remembers it already sent the no_reading message.
    """
    if last_seen is None:
        return True
    return now - last_seen >= timedelta(minutes=SENSOR_OFFLINE_ALERT_MIN)


def status_colour(temp_c: float | None) -> str:
    """Dashboard colour: green >= 18°C, amber 13-18°C, red < 13°C, grey = no reading."""
    if temp_c is None:
        return "grey"
    if temp_c >= TARGET_C:
        return "green"
    if temp_c >= AMBER_FROM_C:
        return "amber"
    return "red"


def room_in_use(now: datetime) -> Room:
    """The room that matters right now: the living room during the day
    (07:00-22:00), the bedroom at night. Nobody is in an unheated living
    room at 04:00."""
    start, end = DAYTIME_WINDOW
    return "living" if start <= now.time() < end else "bedroom"


def home_colour(room_temps: dict[Room, float | None], now: datetime) -> str:
    """The home's headline colour: the colour of the room in use."""
    return status_colour(room_temps.get(room_in_use(now)))


def weekly_summary(summaries: list[NightSummary]) -> WeeklySummary:
    """The family's weekly summary, from the last 7 nights' summaries.

    The count and the text use the same measure: 2+ hours below 18°C. The
    lowest temperature is reported separately. Nights with no readings are
    left out, so "4 of 5 nights" means 5 nights had readings.
    """
    last_week = [s for s in summaries[-7:] if s.min_c is not None and not s.no_reading]
    cold = sum(1 for s in last_week if morning_check_due(s))
    nights = [(s.night_of, s.min_c, s.hours_below_18) for s in last_week]
    text = f"Bedroom below 18°C for 2+ hours on {cold} of {len(last_week)} nights"
    if nights:
        text += f" (lowest {min(n[1] for n in nights):.1f}°C)"
    return WeeklySummary(cold, nights, text)
