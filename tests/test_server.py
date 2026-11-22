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
    assert len(data["homes"]) == 7
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


def test_reply_resumes_clock(app_and_state, monkeypatch):
    """With PAUSE_FOR_MESSAGES on, a message stops the clock and a reply restarts it."""
    monkeypatch.setattr(server, "PAUSE_FOR_MESSAGES", True)
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


def _message(to="person"):
    return server.api.OutgoingMessage("margaret", to, "t1", "Are you warm enough?", "test")


def test_load_phones(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PHONES", {})
    monkeypatch.setattr(server, "PHONE_TO_HOME", {})
    path = tmp_path / "phones.json"
    path.write_text('{"frank": {"person": "+447700900002", "contact": "+447700900102"}}')
    server.load_phones(path)
    assert server.PHONE_TO_HOME == {"+447700900002": "frank"}
    assert server.PHONES["frank"]["contact"] == "+447700900102"


def test_send_email_posts_to_resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.setitem(server.EMAILS, "margaret", {"contact": "son@example.com"})
    calls = []

    class Thread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(server.threading, "Thread", Thread)
    monkeypatch.setattr(server.requests, "post",
                        lambda url, **kw: calls.append((url, kw)) or type("R", (), {"status_code": 200})())
    server.send_email(_message("contact"))
    server.send_email(_message("person"))            # no person address: skipped
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == server.RESEND_URL
    assert kw["headers"]["Authorization"] == "Bearer re_test"
    assert kw["json"]["to"] == ["son@example.com"]
    assert kw["json"]["from"] == server.RESEND_DEFAULT_FROM
    assert kw["json"]["text"] == "Are you warm enough?"


def test_demo_keeps_running_after_a_message(app_and_state):
    """By default the demo clock keeps going when a message goes to the person,
    and a reply still reaches the conversation."""
    client, state = app_and_state
    client.post("/api/clock", json={"action": "jump", "to": at(22, 21).isoformat()})
    client.post("/api/clock", json={"action": "play"})
    state.tick()                                     # Margaret's check-in is due at 21:00
    demo = client.get("/api/family/margaret/demo").get_json()
    assert demo["clock"]["running"] and not demo["clock"]["paused_for_message"]
    assert demo["awaiting_reply"] == "daytime_checkin"
    client.post("/api/reply", json={"home_id": "margaret", "text": "1"})
    assert state.convo["margaret"].last_reply == "1"
    assert state.convo["margaret"].awaiting_reply is None
