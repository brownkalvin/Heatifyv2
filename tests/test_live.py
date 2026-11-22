"""Live mode: sign-up, settings, real readings, the weekly summary and learning."""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

import server
from warmly.model import api
from warmly.model.homes import build_home
from warmly.model.room_model import simulate
from warmly.model.types import HeatingWindow, Reading, RoomParams
from warmly.sources import SimClock
from helpers import at, constant_forecast

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------- #
# A week of readings
# --------------------------------------------------------------------------- #
class WeekSource:
    """A ReadingSource holding fixed readings (both rooms the same)."""

    def __init__(self, readings):
        self.readings = readings

    def get_reading(self, home_id, room):
        return self.readings[-1]

    def get_history(self, home_id, room, since):
        return [r for r in self.readings if r.timestamp >= since]


def _week(heated_nights=(15, 16), tau=30):
    """Nights of 15-21 Nov: 21:00 -> 07:30, cooling with the real tau, or held
    at 18.5°C on `heated_nights`. Outside is a steady -1°C."""
    forecast = constant_forecast(-1.0, at(14, 12), hours=24 * 10)
    real = RoomParams(tau, 45, 2.0, 300)
    readings = []
    for day in range(15, 22):
        heating = [HeatingWindow(time(21), time(8), 18.5)] if day in heated_nights else []
        curve = simulate(18.5, at(day, 21), at(day + 1, 7, 30), real, heating, forecast)
        readings += [Reading("sam", "bedroom", when, round(t, 1), "esp32") for when, t, _ in curve]
    return WeekSource(readings), forecast


def _sam():
    return build_home("sam", "Sam", 80, "Iffley", "flat", sap_score=72)   # bedroom preset tau 60


def test_learn_from_history():
    source, forecast = _week()
    learned = api.learn_from_history(_sam(), at(22, 8), source, forecast)
    assert (learned.habit_on_nights, learned.nights_known) == (2, 7)
    assert learned.habit_text == "heating was on 2 of the last 7 nights"
    assert 25.5 <= learned.fitted_tau["bedroom"] <= 34.5          # really 30 h, preset said 60
    bedroom = learned.rooms["bedroom"]
    capacity = _sam().rooms["bedroom"].heat_loss_w_per_c * _sam().rooms["bedroom"].tau_h
    assert bedroom.heat_loss_w_per_c * bedroom.tau_h == pytest.approx(capacity)   # C kept


def test_weekly_summary_message():
    source, forecast = _week()
    home = _sam()
    sunday_6pm = at(22, 18)                       # 22 Nov 2026 is a Sunday
    convo = {"sam": api.ConversationState("sam", relationship_word="Dad")}
    for day in (17, 18, 19):
        convo["sam"].sent.append((at(day, 21), "daytime_checkin"))
    convo["sam"].replies += [(at(17, 21, 5), "2"), (at(18, 21, 5), "1")]

    def weekly(now):
        return [m for m in api.pending_messages(now, [home], source, forecast, convo)
                if m.template_id.startswith("family_weekly")]

    [message] = weekly(sunday_6pm)
    assert message.to == "contact"
    assert message.text.startswith("Dad's bedroom below 18°C for 2+ hours on 5 of 7 nights")
    assert message.text.endswith("They've answered 2 of 3 check-ins.")
    api.record_sent(convo["sam"], message, sunday_6pm)
    assert weekly(sunday_6pm + timedelta(hours=1)) == []        # once a week
    assert weekly(at(23, 18)) == []                             # Monday: not the day
    assert weekly(at(22, 15)) == []                             # before 17:00


def test_sign_up_rules():
    today = date(2026, 11, 22)
    by_form = api.sign_up(api.SignUp("Sam", 80, "Iffley", property_type="semi", sap_score=60), today)
    assert by_form.rating_from == "form" and by_form.home.rooms["living"].tau_h == pytest.approx(50, abs=1)
    assert "head height" in by_form.placement_text

    cert = json.loads((FIXTURES / "epc_sample.json").read_text(encoding="utf-8"))
    looked_up = api.sign_up(api.SignUp("Sam", 80, "Iffley", postcode="AB1 2CD",
                                       address_line="1 Example Road", epc_consent=True),
                            today, lookup=lambda postcode, line: cert)
    assert looked_up.rating_from == "epc_lookup"
    assert (looked_up.home.property_type, looked_up.home.sap_score) == ("bungalow", 63)
    assert looked_up.certificate_is_old and any("2010" in note for note in looked_up.notes)

    # No consent: the lookup is never called
    with pytest.raises(ValueError):
        api.sign_up(api.SignUp("Sam", 80, "Iffley", postcode="AB1 2CD",
                               address_line="1 Example Road"), today,
                    lookup=lambda *a: pytest.fail("looked up without consent"))
    with pytest.raises(ValueError):
        api.sign_up(api.SignUp("Sam", 80, "Iffley", property_type="castle"), today)
    unknown = api.sign_up(api.SignUp("Sam", 80, "Iffley", property_type="flat"), today)
    assert unknown.rating_from == "default" and unknown.notes


# --------------------------------------------------------------------------- #
# The server in live mode
# --------------------------------------------------------------------------- #
@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "readings.db")
    monkeypatch.setattr(server, "PHONES", {})
    monkeypatch.setattr(server, "PHONE_TO_HOME", {})
    monkeypatch.setattr(server, "EMAILS", {})
    clock = SimClock(at(22, 20))
    clock.pause()
    state = server.WarmlyServer(constant_forecast(-1.0, at(21, 0), hours=72), "live",
                                mode="live", data_dir=tmp_path, clock=clock,
                                refresh_forecast=False)
    return server.create_app(state, ticker=False).test_client(), state, tmp_path


SIGN_UP = {"name": "Sam", "age": 80, "area": "Iffley", "property_type": "semi", "sap_score": 60,
           "sensors": [{"id": "esp-1", "type": "esp32", "room": "bedroom"}],
           "person_phone": "+447700900009", "contact_name": "Alex", "relationship_word": "Dad"}


def test_real_reading_reaches_dashboard(live):
    client, state, data_dir = live
    created = client.post("/api/homes", json=SIGN_UP)
    assert created.status_code == 201
    body = created.get_json()
    assert body["home_id"] == "sam" and body["live"] and body["rating_from"] == "form"

    reading = {"sensor_id": "esp-1", "temp_c": 17.2, "ts": at(22, 19, 45).isoformat()}
    assert client.post("/api/reading", json=reading).status_code == 204
    home = client.get("/api/dashboard").get_json()["homes"][0]
    rooms = {r["room"]: r for r in home["rooms"]}
    assert rooms["bedroom"]["temp_c"] == 17.2 and rooms["bedroom"]["colour"] == "amber"
    assert rooms["living"]["colour"] == "grey"                    # no living-room sensor
    assert "estimate" not in home["status_text"]                   # the bedroom has a reading
    assert server.PHONE_TO_HOME == {"+447700900009": "sam"}
    assert state.convo["sam"].relationship_word == "Dad"

    # Saved: a restarted server finds the home again
    again = server.WarmlyServer(state.forecast, "live", mode="live", data_dir=data_dir,
                                clock=state.clock, refresh_forecast=False)
    assert [h.home_id for h in again.homes] == ["sam"]

    # A second Sam gets a different id; the clock can't be moved in live mode
    assert client.post("/api/homes", json={**SIGN_UP, "sensors": []}).get_json()["home_id"] == "sam-2"
    assert client.post("/api/clock", json={"action": "play"}).status_code == 400


def test_settings(live):
    client, state, _ = live
    client.post("/api/homes", json=SIGN_UP)
    updated = client.post("/api/homes/sam/settings",
                          json={"night_alerts_opted_in": True, "contact_phone": "+447700900010"})
    assert updated.get_json()["night_alerts_opted_in"] is True
    assert updated.get_json()["phones"] == {"person": "+447700900009", "contact": "+447700900010"}
    assert state.convo["sam"].night_alerts_opted_in is True
    assert client.post("/api/homes/nobody/settings", json={}).status_code == 404
    assert client.post("/api/homes", json={"name": "Sam"}).status_code == 400
    bad_sensor = {**SIGN_UP, "sensors": [{"id": "x", "type": "dht11", "room": "bedroom"}]}
    assert client.post("/api/homes", json=bad_sensor).status_code == 400


def test_sign_up_emails(live):
    """Email addresses from sign-up and settings go to emails.json, which the
    Resend sender reads. A bad address is refused before anything is saved."""
    client, state, data_dir = live
    bad = client.post("/api/homes", json={**SIGN_UP, "contact_email": "alex at example"})
    assert bad.status_code == 400 and "email" in bad.get_json()["error"]
    assert state.records == {} and not (data_dir / "homes.json").exists()

    created = client.post("/api/homes", json={**SIGN_UP, "person_email": " sam@example.com ",
                                              "contact_email": "alex@example.co.uk"})
    assert created.status_code == 201
    expected = {"person": "sam@example.com", "contact": "alex@example.co.uk"}
    assert server.EMAILS["sam"] == expected
    assert json.loads((data_dir / "emails.json").read_text())["sam"] == expected

    # Settings: change one, remove the other with "", refuse a bad one
    updated = client.post("/api/homes/sam/settings",
                          json={"contact_email": "alex2@example.com", "person_email": ""})
    assert updated.get_json()["emails"] == {"contact": "alex2@example.com"}
    refused = client.post("/api/homes/sam/settings", json={"contact_email": "nope"})
    assert refused.status_code == 400
    assert server.EMAILS["sam"] == {"contact": "alex2@example.com"}


@pytest.mark.parametrize("address", ["a@b", "@example.com", "a b@example.com", "a@@b.com", "a@.com"])
def test_check_email_refuses(address):
    with pytest.raises(ValueError):
        server.check_email(address)


def test_demo_settings_survive_reset(tmp_path, monkeypatch):
    from warmly.model.forecast import load_fallback
    monkeypatch.setattr(server, "PHONES", {})
    monkeypatch.setattr(server, "PHONE_TO_HOME", {})
    monkeypatch.setattr(server, "EMAILS", {})
    state = server.WarmlyServer(load_fallback(date(2026, 11, 22)), "fallback", date(2026, 11, 22),
                                data_dir=tmp_path)
    client = server.create_app(state, ticker=False).test_client()
    client.post("/api/homes/margaret/settings", json={"relationship_word": "Mum",
                                                      "night_alerts_opted_in": True})
    client.post("/api/clock", json={"action": "reset"})
    assert state.convo["margaret"].relationship_word == "Mum"
    assert state.convo["margaret"].night_alerts_opted_in
    # Signing up during the demo saves the home for live mode, without changing the demo
    response = client.post("/api/homes", json=SIGN_UP).get_json()
    assert response["live"] is False and len(state.homes) == 7


def test_signup_lookup_needs_consent(live, monkeypatch):
    client, _, _ = live
    assert client.post("/api/signup/lookup", json={"postcode": "AB1 2CD"}).status_code == 400
    monkeypatch.setattr(server, "search_epc", lambda postcode: [
        {"address": "1, Example Road", "certificate_number": "c1", "band": "D",
         "registered": "2010-10-13", "uprn": 1},
        {"address": "11, Example Road", "certificate_number": "c11", "band": "C",
         "registered": "2019-02-02", "uprn": 11}])
    found = client.post("/api/signup/lookup", json={"postcode": "AB1 2CD", "consent": True,
                                                    "address_line": "1 Example Road"}).get_json()
    assert len(found["candidates"]) == 2
    assert [c["certificate_number"] for c in found["matching"]] == ["c1"]


def test_status_says_estimate_without_bedroom_sensor(live):
    client, _, _ = live
    client.post("/api/homes", json={**SIGN_UP, "sensors": [{"id": "esp-9", "type": "esp32",
                                                             "room": "living"}]})
    client.post("/api/reading", json={"sensor_id": "esp-9", "temp_c": 19.5,
                                      "ts": at(22, 19, 50).isoformat()})
    home = client.get("/api/dashboard").get_json()["homes"][0]
    assert home["tonight"]["assumed_start"] is True
    assert home["status_text"].endswith("(estimate: no bedroom reading)")
