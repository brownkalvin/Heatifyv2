import json
from datetime import date, datetime, timedelta

from warmly.model import api
from warmly.model.forecast import load_fallback
from helpers import at

NIGHT = date(2026, 11, 22)


def _demo():
    return api.start_demo(load_fallback(NIGHT), NIGHT)


def _dashboard(demo, when):
    demo.clock.jump_to(when)
    return api.dashboard_state(when, demo.homes, demo.source, demo.forecast)


def test_dashboard_json():
    demo = _demo()
    homes = _dashboard(demo, at(22, 21))
    assert [h.home_id for h in homes] == ["margaret", "frank", "joan", "ray", "pat", "eileen"]
    data = json.loads(json.dumps(api.to_jsonable(homes)))
    assert datetime.fromisoformat(data[0]["rooms"][0]["last_seen"]).tzinfo is not None
    ray = next(h for h in homes if h.home_id == "ray")
    assert [r.colour for r in ray.rooms] == ["grey", "grey"]
    assert ray.status_text.startswith("No reading")
    margaret = homes[0]
    assert margaret.rooms[0].colour == "amber"
    assert len(margaret.rooms[0].predicted_curve) == 49       # 12 h of 15-minute steps
    assert margaret.week and margaret.cost_card.summary_text


def test_predicts_before_it_gets_cold():
    """§4.9: at 17:00 Margaret's card says when her living room will cross 18°C."""
    demo = _demo()
    margaret = _dashboard(demo, at(22, 17))[0]
    crossing = margaret.rooms[0].crosses_18_at
    assert at(22, 18, 30) <= crossing <= at(22, 19)
    assert margaret.status_text == f"Below 18°C at ~{crossing:%H:%M} if heating stays off"


def test_pending_pure():
    """Same inputs twice -> identical output (and the network is blocked)."""
    demo = _demo()
    demo.clock.jump_to(at(22, 21))
    convo = {h.home_id: api.ConversationState(h.home_id) for h in demo.homes}
    first = api.pending_messages(at(22, 21), demo.homes, demo.source, demo.forecast, convo)
    second = api.pending_messages(at(22, 21), demo.homes, demo.source, demo.forecast, convo)
    assert first == second and first
    assert all(c.sent == [] for c in convo.values())           # nothing was recorded


def _run_night(reply="2"):
    """The demo night through pending_messages, every check-in answered at once."""
    demo = _demo()
    convo = {h.home_id: api.ConversationState(h.home_id) for h in demo.homes}
    sent = []
    now = demo.start
    while now <= demo.end:
        demo.clock.jump_to(now)
        for message in api.pending_messages(now, demo.homes, demo.source, demo.forecast, convo):
            api.record_sent(convo[message.home_id], message, now)
            sent.append((now, message))
            if convo[message.home_id].awaiting_reply:
                api.record_reply(convo[message.home_id], reply, now)
        now += timedelta(minutes=15)
    return sent


def test_demo_night_messages():
    sent = _run_night()

    def templates(home_id):
        return [m.template_id for _, m in sent if m.home_id == home_id]

    # Margaret: one daytime check-in, its cost follow-up, no separate bedtime
    assert templates("margaret") == ["daytime_checkin", "cost_followup", "morning"]
    # Frank: nothing at all
    assert templates("frank") == []
    # Ray: the bedtime message from his last reading, then the family hears
    # the thermometers have gone quiet
    assert templates("ray") == ["bedtime", "no_reading"]
    # Eileen: one bedtime message (her heating went off at 20:00), one morning
    assert templates("eileen") == ["bedtime", "morning"]
    eileen_bedtime = next((when, m) for when, m in sent
                          if m.home_id == "eileen" and m.template_id == "bedtime")
    assert at(22, 20, 30) <= eileen_bedtime[0] < at(22, 22)
    assert "about 12°C" in eileen_bedtime[1].text or "about 11.5°C" in eileen_bedtime[1].text
    # Nothing goes to the person at night
    for when, message in sent:
        if message.to == "person":
            assert at(22, 7) <= when < at(22, 22) or at(23, 7) <= when < at(23, 22)


def test_escalation_when_no_reply():
    """No reply to a check-in: a second text after 30 minutes, then the family."""
    demo = _demo()
    convo = {h.home_id: api.ConversationState(h.home_id) for h in demo.homes}
    steps = []
    now = at(22, 21)
    while now <= at(22, 22, 30):
        demo.clock.jump_to(now)
        for message in api.pending_messages(now, demo.homes, demo.source, demo.forecast, convo):
            api.record_sent(convo[message.home_id], message, now)
            if message.home_id == "margaret":
                steps.append((now, message.template_id))
        now += timedelta(minutes=15)
    assert steps == [(at(22, 21), "daytime_checkin"), (at(22, 21, 30), "checkin_reminder"),
                     (at(22, 22), "family_evening")]   # the family may get it at night


def test_bedtime_options():
    demo = _demo()
    demo.clock.jump_to(at(22, 20, 30))
    eileen = next(h for h in demo.homes if h.home_id == "eileen")
    options = {o.scenario: o for o in api.bedtime_options(eileen, at(22, 20, 30), demo.source,
                                                          demo.forecast)}
    assert options["off"].cost_p == 0
    assert options["hold_target_overnight"].predicted_min_c == 18.0
    assert 80 <= options["hold_target_overnight"].cost_p <= 92       # the doc's 86p
    assert options["off"].predicted_min_c < options["warm_1h_before_bed"].predicted_min_c


def test_frank_green_from_the_start():
    """With 90 minutes of history before 16:00, Frank's heating is seen at
    once, so his 17:00 card doesn't warn about a cold night."""
    demo = _demo()
    frank = _dashboard(demo, at(22, 17))[1]
    assert frank.rooms[1].heating_on is True
    assert frank.status_text == "Living room warm"


def test_habit_skips_bedtime():
    """Rule 3: a home that heats 5+ nights of 7 gets no bedtime message."""
    demo = _demo()
    demo.clock.jump_to(at(22, 20, 30))
    usual = {"pat": api.ConversationState("pat", habit_on_nights=6)}
    messages = api.pending_messages(at(22, 20, 30), [demo.homes[4]], demo.source,
                                    demo.forecast, usual)
    assert not any(m.template_id == "bedtime" for m in messages)
