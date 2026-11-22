import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import pytest

import server
from warmly.model.forecast import load_fallback
from helpers import at

NIGHT = date(2026, 11, 22)
CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "warmly.ts"


@pytest.fixture
def app_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "readings.db")
    state = server.DemoServer(load_fallback(NIGHT), "fallback", NIGHT)
    app = server.create_app(state, ticker=False)
    return app.test_client(), state


def _ts_fields(interface: str) -> set[str]:
    """Field names of one interface in contracts/warmly.ts."""
    text = CONTRACT.read_text(encoding="utf-8")
    body = re.search(r"export interface " + interface + r"\b[^{]*\{(.*?)\n\}", text, re.S).group(1)
    return set(re.findall(r"^\s+(\w+)\??:", body, re.M))


def test_dashboard_shape(app_and_state):
    client, _ = app_and_state
    data = client.get("/api/dashboard").get_json()
    assert data["api_version"] == "1"
    assert len(data["homes"]) == 6
    assert datetime.fromisoformat(data["sim_time"]) == at(22, 16)
    assert "T" in data["sim_time"] and data["sim_time"].endswith("+00:00")
    assert data["clock"] == {"running": False, "paused_for_message": False, "speed": 720}


def test_contract_keys(app_and_state):
    """/api/dashboard's JSON keys match contracts/warmly.ts, so they can't drift."""
    client, state = app_and_state
    state.demo.clock.jump_to(at(22, 21))
    data = client.get("/api/dashboard").get_json()
    home = data["homes"][0]
    assert set(data) == _ts_fields("DashboardResponse")
    assert set(data["clock"]) == _ts_fields("ClockState")
    assert set(home) == _ts_fields("HomeState")
    assert set(home["rooms"][0]) == _ts_fields("RoomState")
    assert set(home["tonight"]) == _ts_fields("OvernightPrediction")
    assert set(home["cost_card"]) == _ts_fields("CostCard")
    if home["week"]:
        assert set(home["week"][0]) == _ts_fields("NightOutlook")


def test_reply_resumes_clock(app_and_state):
    client, state = app_and_state
    client.post("/api/clock", json={"action": "jump", "to": at(22, 21).isoformat()})
    client.post("/api/clock", json={"action": "play"})
    state.tick()                                     # Margaret's check-in is due at 21:00
    clock = client.get("/api/dashboard").get_json()["clock"]
    assert clock["paused_for_message"] and clock["running"]
    assert any(m["home_id"] == "margaret" and m["template_id"] == "daytime_checkin"
               for m in client.get("/api/messages").get_json())

    reply = client.post("/api/reply", json={"home_id": "margaret", "text": "2"}).get_json()
    assert reply["messages"][-1]["direction"] == "in"
    assert client.get("/api/dashboard").get_json()["clock"]["paused_for_message"] is False
    assert client.post("/api/reply", json={"home_id": "nobody", "text": "2"}).status_code == 400


def test_reset_deterministic(app_and_state):
    client, _ = app_and_state
    client.post("/api/clock", json={"action": "reset"})
    first = client.get("/api/dashboard").get_json()
    client.post("/api/clock", json={"action": "jump", "to": at(22, 23).isoformat()})
    client.post("/api/clock", json={"action": "reset"})
    assert client.get("/api/dashboard").get_json() == first


def test_cors_header(app_and_state):
    client, _ = app_and_state
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in other.headers
    assert ok.get_json() == {"ok": True, "forecast_source": "fallback"}


def test_home_detail_and_clock_errors(app_and_state):
    client, _ = app_and_state
    detail = client.get("/api/homes/eileen").get_json()
    assert detail["home_id"] == "eileen" and detail["messages"] == []
    assert client.get("/api/homes/nobody").status_code == 404
    assert client.post("/api/clock", json={"action": "fly"}).status_code == 400
    assert client.post("/api/clock", json={"action": "jump", "to": "2026-11-22T20:00"}).status_code == 400


def test_reading_endpoint(app_and_state):
    client, _ = app_and_state
    good = {"sensor_id": "esp-1", "temp_c": 17.4, "ts": "2026-11-22T20:00:00+00:00"}
    assert client.post("/api/reading", json=good).status_code == 204
    assert client.post("/api/reading", json={**good, "ts": "2026-11-22T20:00:00"}).status_code == 400
    with closing(sqlite3.connect(server.DB_PATH)) as conn:
        assert conn.execute("SELECT sensor_id, temp_c FROM readings").fetchall() == [("esp-1", 17.4)]


def test_sms_reply(app_and_state, monkeypatch):
    client, state = app_and_state
    monkeypatch.setitem(server.PHONE_TO_HOME, "+447700900001", "margaret")
    response = client.post("/sms/reply", data={"From": "+447700900001", "Body": "1"})
    assert response.mimetype == "text/xml" and b"<Response/>" in response.data
    assert state.convo["margaret"].last_reply == "1"


def _twilio_env(monkeypatch):
    for key, value in (("TWILIO_ACCOUNT_SID", "AC123"), ("TWILIO_AUTH_TOKEN", "tok"),
                       ("TWILIO_FROM", "+447700900000")):
        monkeypatch.setenv(key, value)


def _message(to="person"):
    return server.api.OutgoingMessage("margaret", to, "t1", "Are you warm enough?", "test")


def test_send_sms_posts_to_twilio(monkeypatch):
    _twilio_env(monkeypatch)
    monkeypatch.setitem(server.PHONES, "margaret", {"person": "+447700900001"})
    calls = []

    class Thread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(server.threading, "Thread", Thread)
    monkeypatch.setattr(server.requests, "post",
                        lambda url, **kw: calls.append((url, kw)) or type("R", (), {"status_code": 201})())
    server.send_sms(_message())
    url, kw = calls[0]
    assert url.endswith("/Accounts/AC123/Messages.json")
    assert kw["auth"] == ("AC123", "tok")
    assert kw["data"] == {"To": "+447700900001", "From": "+447700900000",
                          "Body": "Are you warm enough?"}


def test_send_sms_skips_without_config_or_number(monkeypatch):
    monkeypatch.setattr(server.requests, "post", lambda *a, **k: pytest.fail("posted"))
    for key in server.TWILIO_KEYS:
        monkeypatch.delenv(key, raising=False)
    server.send_sms(_message())                      # no Twilio config
    _twilio_env(monkeypatch)
    monkeypatch.setitem(server.PHONES, "margaret", {"person": "+447700900001"})
    server.send_sms(_message("contact"))             # no contact number


def test_load_phones(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PHONES", {})
    monkeypatch.setattr(server, "PHONE_TO_HOME", {})
    path = tmp_path / "phones.json"
    path.write_text('{"frank": {"person": "+447700900002", "contact": "+447700900102"}}')
    server.load_phones(path)
    assert server.PHONE_TO_HOME == {"+447700900002": "frank"}
    assert server.PHONES["frank"]["contact"] == "+447700900102"
