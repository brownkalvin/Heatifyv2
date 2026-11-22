"""THE contract between the model and the other teams (design doc §4.11).

The front-end (through server.py) and the backend should only import this
file. Everything returned is a plain dataclass; to_jsonable() turns it into
JSON-ready dicts with ISO-8601 datetimes.

This package DECIDES; the backend ACTS. dashboard_state() and
pending_messages() are pure: same inputs, same outputs, nothing sent and
nothing stored. The backend owns each home's ConversationState, sends the
messages, then calls record_sent() / record_reply() to update it.
"""
import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from warmly.sources import ReadingSource, SimClock, SyntheticSource

from .cost import ROOM_NAMES, build_cost_card, cost_of_scenario
from .forecast import (OXFORD_LATLON, freezing_tonight, get_cold_night, get_historical_with_source,
                       get_hourly_forecast_with_source, load_fallback, snow_tonight, temp_at,
                       tonight)
from .calibrate import learn_tau, params_with_fitted_tau
from .epc_lookup import certificate_is_old, epc_to_profile_fields, find_epc
from .homes import (BEDTIME, BEDTIME_CHECK, COST_CARD_UNTIL, DAYTIME_WINDOW,
                    INFER_WINDOW_MIN, LONDON, PROPERTY_TYPES, REPLY_TIMEOUT_MIN, TARGET_C,
                    WEEKLY_SUMMARY_WEEKDAY, build_home)
from .infer_heating import heating_habit, infer_heating, label_night
from .messages import AUDIENCE, EXPECTS_REPLY, display_pence, display_temp, render
from .predict import (daytime_status, predict_crossing, predict_if, predict_now, predict_week,
                      summarise_night)
from .room_model import simulate
from .rules import (bedtime_message_due, daytime_check_due, morning_check_due, night_alert_due,
                    one_conversation_per_evening, room_in_use, sensor_offline_due,
                    status_colour, weekly_summary)
from .synthetic import build_demo_homes, demo_window, export_csv
from .types import (CostCard, ForecastPoint, HomeProfile, NightOutlook, OvernightPrediction,
                    Reading, Room, RoomParams)

API_VERSION = "1"
ROOMS: tuple[Room, Room] = ("living", "bedroom")


# --------------------------------------------------------------------------- #
# The shapes (mirrored by hand in contracts/warmly.ts)
# --------------------------------------------------------------------------- #
@dataclass
class RoomState:
    room: Room
    temp_c: Optional[float]                       # None = no reading
    last_seen: Optional[datetime]
    colour: str                                   # "green" | "amber" | "red" | "grey"
    heating_on: Optional[bool]
    crosses_18_at: Optional[datetime]             # "below 18°C at ~18:45 if heating stays off"
    predicted_curve: list[tuple[datetime, float]]  # heating-off, next 12 h: the dashed line
    history: list[tuple[datetime, Optional[float]]]  # last 24 h: the solid line


@dataclass
class HomeState:
    home_id: str
    name: str
    area: str
    rooms: list[RoomState]
    tonight: OvernightPrediction                  # bedroom, heating-off case
    week: list[NightOutlook]
    cost_card: CostCard
    out_now_c: float
    out_low_tonight_c: float
    snow_tonight: bool
    status_text: str                              # one line for the home card


@dataclass
class ConversationState:
    """OWNED BY THE BACKEND, passed in every call. record_sent() and
    record_reply() keep it up to date."""
    home_id: str
    messaged_person_since_17: bool = False
    awaiting_reply: Optional[str] = None          # template_id of the last unanswered question
    sent_at: Optional[datetime] = None
    last_reply: Optional[str] = None              # "1" | "2" | "HELP" | "OK" | free text
    declines_recent: int = 0
    night_alerts_opted_in: bool = False
    contact_name: str = ""
    # Added in the build (not in the doc's first draft), so pending_messages
    # never repeats itself:
    last_template: Optional[str] = None           # the template the last reply answered
    replied_at: Optional[datetime] = None
    sent: list[tuple[datetime, str]] = field(default_factory=list)   # (when, template_id)
    relationship_word: str = ""                   # e.g. "Mum", used in messages to the contact
    habit_on_nights: int = 0                      # nights heated of the last 7 (heating_habit()[0])
    replies: list[tuple[datetime, str]] = field(default_factory=list)   # (when, text)


@dataclass
class OutgoingMessage:
    home_id: str
    to: str                                       # "person" | "contact"
    template_id: str
    text: str
    reason: str                                   # e.g. "daytime: below 18°C for 2 h, not warming"


@dataclass
class BedtimeOption:
    scenario: str                                 # "off" | "hold_target_overnight" | "warm_1h_before_bed"
    predicted_min_c: float
    predicted_at_7am_c: float
    cost_p: float


@dataclass
class DemoNight:
    """Everything the demo needs, built once (server.py and the demo script)."""
    night_of: date
    homes: list[HomeProfile]
    forecast: list[ForecastPoint]
    clock: SimClock
    source: SyntheticSource
    start: datetime
    end: datetime


# --------------------------------------------------------------------------- #
# Setting up the demo
# --------------------------------------------------------------------------- #
def load_demo_forecast(mode: str = "live",
                       replay: date | None = None) -> tuple[list[ForecastPoint], str, date]:
    """The forecast for the demo night: (points, source, night_of).

    mode "live" (falls back to the cache, then the hard-coded night),
    "offline" (the hard-coded night), "cold" (last winter's coldest night)
    or "replay" (the night of `replay`). source is "live", "cache",
    "fallback" or "cold_night". Never raises.
    """
    today = date.today()
    if mode == "offline":
        return load_fallback(today), "fallback", today
    if mode == "cold":
        return get_cold_night()
    if mode == "replay" and replay is not None:
        points, source = get_historical_with_source(*OXFORD_LATLON, replay,
                                                    replay + timedelta(days=1))
        return points, source, replay
    points, source = get_hourly_forecast_with_source(*OXFORD_LATLON)
    return points, source, today


def load_live_forecast() -> tuple[list[ForecastPoint], str]:
    """The next 7 days plus the last 7 (learning needs last week's outdoor
    temperatures): (points, source). Never raises."""
    return get_hourly_forecast_with_source(*OXFORD_LATLON, days=7, past_days=7)


def start_demo(forecast: list[ForecastPoint], night_of: date, speed: float = 720) -> DemoNight:
    """Build the six demo homes, a paused clock at 16:00 and their readings.

    The readings start INFER_WINDOW_MIN (90 minutes) before 16:00, as a real
    thermometer would have history, so heating inference works from the
    first minute of the demo (otherwise Frank's card warns at 17:00)."""
    homes = build_demo_homes(night_of)
    start, end = demo_window(night_of)
    clock = SimClock(start, speed)
    clock.pause()
    clock.jump_to(start)        # exactly 16:00, not 16:00 plus setup time
    history_start = start - timedelta(minutes=INFER_WINDOW_MIN)
    source = SyntheticSource(homes, forecast, clock, history_start, end)
    return DemoNight(night_of, homes, forecast, clock, source, start, end)


def export_demo_readings(demo: DemoNight, path=None):
    """Write the demo night's readings to data/synthetic_readings.csv (or `path`)."""
    return export_csv(demo.source.curves) if path is None else export_csv(demo.source.curves, path)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def to_jsonable(value):
    """Dataclasses, tuples, datetimes and dates -> plain JSON-ready values
    (datetimes as ISO-8601 with offset, e.g. "2026-11-22T18:45:00+00:00")."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _is_night(now: datetime) -> bool:
    start, end = DAYTIME_WINDOW
    return not start <= now.time() < end


def _evening_start(now: datetime) -> datetime:
    """17:00 on the evening this moment belongs to (03:00 belongs to yesterday's)."""
    day = now.date() - timedelta(days=1) if now.time() < DAYTIME_WINDOW[0] else now.date()
    return datetime.combine(day, time(17), tzinfo=LONDON)


def _day_start(now: datetime) -> datetime:
    """07:00 today, or yesterday's if it's before 07:00."""
    day = now.date() - timedelta(days=1) if now.time() < DAYTIME_WINDOW[0] else now.date()
    return datetime.combine(day, DAYTIME_WINDOW[0], tzinfo=LONDON)


def _sent_since(convo: ConversationState, template_ids: set[str], since: datetime) -> bool:
    return any(when >= since and template_id in template_ids for when, template_id in convo.sent)


def _history(source: ReadingSource, home: HomeProfile, now: datetime) -> dict[Room, list[Reading]]:
    since = now - timedelta(hours=24)
    return {room: source.get_history(home.home_id, room, since) for room in ROOMS}


def _latest_temp(readings: list[Reading], now: datetime) -> Optional[float]:
    """The newest reading at or before `now`, if it has a temperature."""
    earlier = [r for r in readings if r.timestamp <= now]
    return earlier[-1].temp_c if earlier else None


def _out_low(forecast: list[ForecastPoint], now: datetime) -> float:
    points = tonight(forecast, now)
    return min(p.temp_c for p in points) if points else temp_at(forecast, now)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def _room_state(home: HomeProfile, room: Room, readings: list[Reading], now: datetime,
                forecast: list[ForecastPoint]) -> RoomState:
    earlier = [r for r in readings if r.timestamp <= now]
    latest = earlier[-1] if earlier else None
    temp = latest.temp_c if latest else None
    seen = [r.timestamp for r in earlier if r.temp_c is not None]
    params = home.rooms[room]

    heating, crossing, curve = None, None, []
    if temp is not None:
        heating = infer_heating(earlier, forecast, params, now).heating_on
        crossing = predict_crossing(home, room, latest.timestamp, temp, forecast)
        sim = simulate(temp, latest.timestamp, latest.timestamp + timedelta(hours=12),
                       params, [], forecast)
        curve = [(when, round(t, 2)) for when, t, _ in sim]
    return RoomState(room, temp, max(seen) if seen else None, status_colour(temp), heating,
                     crossing, curve, [(r.timestamp, r.temp_c) for r in earlier])


def _cost_card(home: HomeProfile, now: datetime, temps: dict[Room, Optional[float]],
               forecast: list[ForecastPoint]) -> CostCard:
    """Evenings: the living room until 22:30. Later: the bedroom until 7am."""
    if DAYTIME_WINDOW[0] <= now.time() < COST_CARD_UNTIL:
        room, until, label = "living", COST_CARD_UNTIL, "bedtime"
    else:
        room, until, label = "bedroom", DAYTIME_WINDOW[0], "7am"
    t_now = temps[room] if temps[room] is not None else TARGET_C
    return build_cost_card(home, room, now, t_now, forecast, until, label)


def _status_text(rooms: dict[Room, RoomState], tonight_pred: OvernightPrediction,
                 now: datetime) -> str:
    in_use = room_in_use(now)
    room = rooms[in_use]
    name = ROOM_NAMES[in_use]
    if room.temp_c is None:
        since = f" since {room.last_seen:%H:%M}" if room.last_seen else ""
        return f"No reading from the {name}{since}"
    if room.temp_c < TARGET_C:
        return f"{name.capitalize()} {room.temp_c:.1f}°C, below 18°C"
    if room.crosses_18_at is not None and room_in_use(room.crosses_18_at) == in_use:
        # (a living-room crossing after bedtime doesn't matter: nobody's in it)
        return f"Below 18°C at ~{room.crosses_18_at:%H:%M} if heating stays off"
    if tonight_pred.will_breach_18 and rooms["bedroom"].heating_on is not True:
        return (f"Bedroom could drop to about {display_temp(tonight_pred.predicted_min_c)}°C "
                f"overnight if heating stays off")
    return f"{name.capitalize()} warm"


def home_state(home: HomeProfile, now: datetime, source: ReadingSource,
               forecast: list[ForecastPoint]) -> HomeState:
    """One home's card for the dashboard."""
    readings = _history(source, home, now)
    rooms = {room: _room_state(home, room, readings[room], now, forecast) for room in ROOMS}
    temps = {room: rooms[room].temp_c for room in ROOMS}
    tonight_pred = predict_now(home, "bedroom", readings["bedroom"], now, forecast)
    bedroom_now = temps["bedroom"] if temps["bedroom"] is not None else TARGET_C
    return HomeState(
        home_id=home.home_id, name=home.name, area=home.area,
        rooms=[rooms[room] for room in ROOMS],
        tonight=tonight_pred,
        week=predict_week(home, "bedroom", now, bedroom_now, forecast),
        cost_card=_cost_card(home, now, temps, forecast),
        out_now_c=round(temp_at(forecast, now), 1),
        out_low_tonight_c=round(_out_low(forecast, now), 1),
        snow_tonight=snow_tonight(forecast, now),
        status_text=_status_text(rooms, tonight_pred, now),
    )


def dashboard_state(now: datetime, homes: list[HomeProfile], source: ReadingSource,
                    forecast: list[ForecastPoint]) -> list[HomeState]:
    """Every home's card at `now`. Pure: reads only what's passed in."""
    return [home_state(home, now, source, forecast) for home in homes]


def bedtime_options(home: HomeProfile, now: datetime, source: ReadingSource,
                    forecast: list[ForecastPoint]) -> list[BedtimeOption]:
    """The bedroom's options tonight: heating off, kept at 18°C, or warmed
    for an hour before bed. Each with its predicted minimum and gas cost."""
    readings = _history(source, home, now)["bedroom"]
    temp = _latest_temp(readings, now)
    t_now = temp if temp is not None else TARGET_C
    options = []
    for scenario in ("off", "hold_target_overnight", "warm_1h_before_bed"):
        p = predict_if(home, "bedroom", now, t_now, forecast, scenario)
        cost = cost_of_scenario(home, "bedroom", now, t_now, forecast, scenario)
        options.append(BedtimeOption(scenario, p.predicted_min_c, p.predicted_at_7am_c, cost))
    return options


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def _escalation(home: HomeProfile, convo: ConversationState, now: datetime,
                living_temp: Optional[float], card: CostCard) -> Optional[OutgoingMessage]:
    """The next step when a question has gone unanswered for REPLY_TIMEOUT_MIN:
    a second text to the person, then a text to the family."""
    if convo.awaiting_reply is None or convo.sent_at is None:
        return None
    if now - convo.sent_at < timedelta(minutes=REPLY_TIMEOUT_MIN):
        return None
    if convo.awaiting_reply != "checkin_reminder":
        if _is_night(now):
            return None   # never text the person at night; the morning check takes over
        return OutgoingMessage(home.home_id, "person", "checkin_reminder",
                               render("checkin_reminder", name=home.name),
                               f"no reply to {convo.awaiting_reply} after {REPLY_TIMEOUT_MIN} min")
    if _sent_since(convo, {"family_evening", "family_urgent"}, convo.sent_at):
        return None   # the family has already been told
    name = convo.relationship_word or home.name
    if now.time() < time(12):
        text = render("family_urgent", name=name)
        template = "family_urgent"
    else:
        temp = display_temp(living_temp) if living_temp is not None else "?"
        text = render("family_evening", name=name, temp=temp,
                      reason="They haven't answered our check-in.",
                      room_cost=display_pence(card.heat_room_tonight_p))
        template = "family_evening"
    return OutgoingMessage(home.home_id, "contact", template, text,
                           "no reply to the reminder either")


def _person_message(home: HomeProfile, convo: ConversationState, now: datetime,
                    readings: dict[Room, list[Reading]], forecast: list[ForecastPoint],
                    card: CostCard) -> Optional[OutgoingMessage]:
    """At most one new message to the person: a follow-up to their reply,
    the morning check, the daytime check-in or the bedtime message."""
    if _is_night(now) or convo.awaiting_reply is not None:
        return None
    living = _latest_temp(readings["living"], now)
    out_low = display_temp(_out_low(forecast, now))

    # A reply of "2" (heating off) to an evening check-in: the cost follow-up.
    # Only in the evening (it talks about "tonight") and only if the heating
    # can run (a broken boiler makes the gas figure zero).
    evening = _evening_start(now)
    if (convo.last_reply == "2" and convo.last_template in ("daytime_checkin",
                                                             "daytime_checkin_predicted")
            and now >= evening and card.heat_room_tonight_p > 0
            and not _sent_since(convo, {"cost_followup"}, convo.replied_at)):
        return OutgoingMessage(home.home_id, "person", "cost_followup", render(
            "cost_followup", name=home.name, room_cost=display_pence(card.heat_room_tonight_p),
            fan_cost=display_pence(card.fan_heater_p),
            blanket=display_pence(card.blanket_per_hour_p)), "replied 2 (heating off)")

    # Morning check: the bedroom was cold for 2+ hours last night
    day_start = _day_start(now)
    if not _sent_since(convo, {"morning"}, day_start):
        summary = summarise_night(readings["bedroom"], day_start.date() - timedelta(days=1))
        if not summary.no_reading and summary.min_c is not None and morning_check_due(summary):
            return OutgoingMessage(home.home_id, "person", "morning", render(
                "morning", name=home.name, night_min=display_temp(summary.min_c)),
                f"bedroom below 18°C for {summary.hours_below_18:.1f} h overnight")

    # One conversation per evening: nothing new if they've been messaged since 17:00
    person_ids = {tid for tid, to in AUDIENCE.items() if to == "person"}
    talked_this_evening = now >= evening and _sent_since(convo, person_ids, evening)

    # Daytime check-in: living room cold for 2+ hours and not warming
    status = daytime_status(readings["living"], now, forecast, home.rooms["living"])
    if (daytime_check_due(status) and not _sent_since(convo, {"daytime_checkin"}, day_start)
            and one_conversation_per_evening(talked_this_evening)):
        return OutgoingMessage(home.home_id, "person", "daytime_checkin", render(
            "daytime_checkin", name=home.name, temp=display_temp(living), out_low=out_low),
            f"daytime: below 18°C for {status.below_18_for_h:.1f} h, not warming")

    # Bedtime message, between 20:30 and 22:00
    if (BEDTIME_CHECK <= now.time() < BEDTIME
            and not _sent_since(convo, {"bedtime", "bedtime_estimate"}, evening)
            and one_conversation_per_evening(talked_this_evening)):
        pred = predict_now(home, "bedroom", readings["bedroom"], now, forecast)
        heating = infer_heating(readings["bedroom"], forecast, home.rooms["bedroom"], now)
        due = bedtime_message_due(
            pred, convo.declines_recent, snow_tonight(forecast, now),
            freezing_tonight(forecast, now), bedroom_heating_on=heating.heating_on,
            habit_on_nights=convo.habit_on_nights,
            living_below_18=living is not None and living < TARGET_C)
        if due and pred.assumed_start:
            return OutgoingMessage(home.home_id, "person", "bedtime_estimate", render(
                "bedtime_estimate", name=home.name, out_low=out_low,
                pred_min=display_temp(pred.predicted_min_c)), "bedtime: no reading, estimate")
        if due:
            start_t = pred.curve[0][1]
            hold = cost_of_scenario(home, "bedroom", now, start_t, forecast, "hold_target_overnight")
            warm = cost_of_scenario(home, "bedroom", now, start_t, forecast, "warm_1h_before_bed")
            return OutgoingMessage(home.home_id, "person", "bedtime", render(
                "bedtime", name=home.name, out_low=out_low,
                pred_min=display_temp(pred.predicted_min_c), hold_cost=display_pence(hold),
                warm_cost=display_pence(warm)),
                f"bedtime: heating-off case falls to {pred.predicted_min_c:.1f}°C")
    return None


def _contact_messages(home: HomeProfile, convo: ConversationState, now: datetime,
                      readings: dict[Room, list[Reading]]) -> list[OutgoingMessage]:
    """Messages to the family that don't depend on the person replying:
    a silent thermometer, and (if opted in) a very cold bedroom at night."""
    messages = []
    name = convo.relationship_word or home.name

    silent = {}
    for room in ROOMS:
        seen = [r.timestamp for r in readings[room] if r.temp_c is not None and r.timestamp <= now]
        last_seen = max(seen) if seen else None
        if sensor_offline_due(last_seen, now):
            silent[room] = last_seen
    if silent:
        known = [t for t in silent.values() if t is not None]
        outage_start = min(known) if known else now - timedelta(hours=24)
        if not _sent_since(convo, {"no_reading"}, outage_start):
            rooms = " and ".join(ROOM_NAMES[room] for room in silent)
            thermometers = rooms + (" thermometers" if len(silent) > 1 else " thermometer")
            last = f"{min(known):%H:%M}" if known else "yesterday"
            messages.append(OutgoingMessage(home.home_id, "contact", "no_reading", render(
                "no_reading", name=name, thermometers=thermometers, last_seen=last),
                f"no reading for {', '.join(silent)}"))

    bedroom = [r for r in readings["bedroom"] if r.timestamp <= now]
    if (bedroom and _is_night(now) and night_alert_due(bedroom[-1], convo.night_alerts_opted_in)
            and not _sent_since(convo, {"family_night"}, _evening_start(now))):
        messages.append(OutgoingMessage(home.home_id, "contact", "family_night", render(
            "family_night", name=name, temp=display_temp(bedroom[-1].temp_c)),
            "night: bedroom below the danger floor"))
    return messages


def _weekly_message(home: HomeProfile, convo: ConversationState, now: datetime,
                    source: ReadingSource) -> Optional[OutgoingMessage]:
    """The family's weekly summary, on WEEKLY_SUMMARY_WEEKDAY from 17:00
    (before 22:00), at most once a week, and only if a night had readings."""
    evening = _evening_start(now)
    if (now.weekday() != WEEKLY_SUMMARY_WEEKDAY or not evening <= now < evening + timedelta(hours=5)
            or _sent_since(convo, {"family_weekly", "family_weekly_quiet"}, now - timedelta(days=6))):
        return None
    bedroom = source.get_history(home.home_id, "bedroom", now - timedelta(days=8))
    nights = [summarise_night(bedroom, now.date() - timedelta(days=n)) for n in range(7, 0, -1)]
    summary = weekly_summary(nights)
    if not summary.nights:
        return None
    week_ago = now - timedelta(days=7)
    asked = sum(1 for when, tid in convo.sent if when >= week_ago and tid in EXPECTS_REPLY)
    answered = min(asked, sum(1 for when, _ in convo.replies if when >= week_ago))
    name = convo.relationship_word or home.name
    text = summary.text.replace("Bedroom", f"{name}'s bedroom", 1)
    if asked:
        message = render("family_weekly", summary=text, answered=answered, asked=asked)
        return OutgoingMessage(home.home_id, "contact", "family_weekly", message, "weekly summary")
    return OutgoingMessage(home.home_id, "contact", "family_weekly_quiet",
                           render("family_weekly_quiet", summary=text), "weekly summary")


def pending_messages(now: datetime, homes: list[HomeProfile], source: ReadingSource,
                     forecast: list[ForecastPoint],
                     convo: dict[str, ConversationState]) -> list[OutgoingMessage]:
    """The messages that are due at `now`. Pure: same inputs give the same
    outputs, and nothing is sent. The backend sends them, calls record_sent()
    for each, and SimClock.pause_for_message() for person-facing ones.
    Nothing goes to the person between 22:00 and 07:00."""
    due = []
    for home in homes:
        state = convo.get(home.home_id) or ConversationState(home.home_id)
        readings = _history(source, home, now)
        temps = {room: _latest_temp(readings[room], now) for room in ROOMS}
        card = _cost_card(home, now, temps, forecast)
        step = _escalation(home, state, now, temps["living"], card)
        if step:
            due.append(step)
        else:
            message = _person_message(home, state, now, readings, forecast, card)
            if message:
                due.append(message)
        due.extend(_contact_messages(home, state, now, readings))
        weekly = _weekly_message(home, state, now, source)
        if weekly:
            due.append(weekly)
    return due


def record_sent(convo: ConversationState, message: OutgoingMessage, now: datetime) -> None:
    """Backend helper: update a home's ConversationState after sending."""
    convo.sent.append((now, message.template_id))
    if message.to == "person":
        evening = _evening_start(now)
        convo.messaged_person_since_17 = now >= evening
        if message.template_id in EXPECTS_REPLY:
            convo.awaiting_reply = message.template_id
            convo.sent_at = now


def record_reply(convo: ConversationState, text: str, now: datetime) -> None:
    """Backend helper: update a home's ConversationState when the person replies."""
    convo.last_reply = text.strip().upper()
    convo.replies.append((now, convo.last_reply))
    convo.last_template = convo.awaiting_reply or convo.last_template
    convo.replied_at = now
    convo.awaiting_reply = None
    convo.sent_at = None


# --------------------------------------------------------------------------- #
# Learning from past nights (design doc §4.3b, §5.1)
# --------------------------------------------------------------------------- #
@dataclass
class LearnedHome:
    home_id: str
    habit_on_nights: int                          # heated nights of the last 7 known
    nights_known: int
    habit_text: str                               # e.g. "heating was on 2 of the last 7 nights"
    rooms: dict[Room, RoomParams]               # with fitted tau where there was enough data
    fitted_tau: dict[Room, Optional[float]]       # None = kept the preset


def learn_from_history(home: HomeProfile, now: datetime, source: ReadingSource,
                       forecast: list[ForecastPoint]) -> LearnedHome:
    """What the last 7 nights say about this home: how often the bedroom
    heating ran overnight (for bedtime rule 3), and each room's real tau.

    Recomputed from the readings every time, so nothing needs storing. Nights
    the forecast doesn't cover are skipped (pass a forecast with past days).
    The backend puts habit_on_nights in ConversationState and uses `rooms`
    for the home ("we start from your energy certificate, then learn").
    """
    since = now - timedelta(days=8)
    start = forecast[0].timestamp if forecast else now
    night_dates = [now.date() - timedelta(days=n) for n in range(7, 0, -1)]
    night_dates = [d for d in night_dates
                   if datetime.combine(d, BEDTIME, tzinfo=LONDON) >= start]
    rooms, fitted = {}, {}
    labels = []
    for room in ROOMS:
        readings = source.get_history(home.home_id, room, since)
        params = home.rooms[room]
        if room == "bedroom":
            labels = [label_night(readings, forecast, params, d) for d in night_dates]
        tau = learn_tau([(d, readings) for d in night_dates], forecast, params)
        fitted[room] = tau if tau != params.tau_h else None
        rooms[room] = params_with_fitted_tau(params, tau) if fitted[room] else params
    on, known, text = heating_habit(labels)
    return LearnedHome(home.home_id, on, known, text, rooms, fitted)


# --------------------------------------------------------------------------- #
# Sign-up (design doc §5.1, §5.2)
# --------------------------------------------------------------------------- #
@dataclass
class SignUp:
    """What the sign-up form collects. Give the energy rating (sap_score or
    epc_band) and property_type yourself, or tick epc_consent with a postcode
    and address to look them up."""
    name: str
    age: int
    area: str
    postcode: str = ""
    address_line: str = ""
    property_type: Optional[str] = None           # one of homes.PROPERTY_TYPES
    sap_score: Optional[int] = None
    epc_band: Optional[str] = None
    floor_area_m2: Optional[float] = None
    epc_consent: bool = False                     # the person (or family) agreed to the lookup
    home_id: Optional[str] = None


@dataclass
class SignUpResult:
    home: HomeProfile
    rating_from: str                              # "form" | "epc_lookup" | "default"
    certificate_lodged: Optional[date]
    certificate_is_old: bool                      # over 10 years: ask about insulation since
    placement_text: str                           # where to put the thermometers
    notes: list[str]


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "home"


def sign_up(form: SignUp, today: date, lookup=find_epc) -> SignUpResult:
    """Turn a sign-up form into a HomeProfile. The form's own answers win;
    the EPC lookup (only with consent) fills what's missing; "don't know"
    falls back to SAP 55. Raises ValueError for an unknown property type."""
    notes = []
    fields = {"property_type": form.property_type, "sap_score": form.sap_score,
              "epc_band": form.epc_band, "floor_area_m2": form.floor_area_m2}
    rating_from = "form" if (form.sap_score or form.epc_band) else "default"
    lodged = None
    if form.epc_consent and form.postcode and form.address_line:
        cert = lookup(form.postcode, form.address_line)
        if cert:
            found = epc_to_profile_fields(cert)
            lodged = found.pop("lodged")
            for key, value in found.items():
                if fields[key] is None:
                    fields[key] = value
            if rating_from == "default":
                rating_from = "epc_lookup"
        else:
            notes.append("No single energy certificate matched that address; "
                         "please give the rating from the certificate, or pick the home.")
    if fields["property_type"] is None:
        raise ValueError("property_type is needed (or a matching energy certificate)")
    if fields["property_type"] not in PROPERTY_TYPES:
        raise ValueError(f"property_type must be one of {sorted(PROPERTY_TYPES)}")
    if rating_from == "default":
        notes.append("No energy rating given: using a typical band D home until the "
                     "thermometers show how it really behaves.")
    old = lodged is not None and certificate_is_old(lodged, today)
    if old:
        notes.append(f"The certificate is from {lodged.year}: has the home been insulated since?")
    home = build_home(form.home_id or _slug(form.name), form.name, form.age, form.area,
                      fields["property_type"], sap_score=fields["sap_score"],
                      epc_band=fields["epc_band"], floor_area_m2=fields["floor_area_m2"])
    return SignUpResult(home, rating_from, lodged, old, render("sensor_placement"), notes)
