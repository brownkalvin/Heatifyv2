"""The family app's endpoints (family.py, web/UI_SPEC.md §5)."""
from datetime import date, datetime

import pytest

import server
from warmly.model.forecast import load_fallback

NIGHT = date(2026, 11, 22)


@pytest.fixture
def client_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "readings.db")
    state = server.WarmlyServer(load_fallback(NIGHT), "fallback", NIGHT, data_dir=tmp_path,
                                refresh_forecast=False)
    state.clock.jump_to(datetime.fromisoformat("2026-11-22T21:00:00+00:00"))
    return server.create_app(state, ticker=False).test_client(), state


def test_readings_shape(client_and_state):
    client, _ = client_and_state
    rows = client.get("/api/family/margaret/readings?since=2026-11-21T21:00:00Z").get_json()
    assert rows and set(rows[0]) == {"time", "temperature"}
    times = [datetime.fromisoformat(r["time"]) for r in rows]
    assert times == sorted(times) and times[-1] <= datetime.fromisoformat("2026-11-22T21:00:00+00:00")


def test_live_status_shape(client_and_state):
    client, _ = client_and_state
    status = client.get("/api/family/margaret/live-status").get_json()
    assert isinstance(status["heatingOn"], bool)
    assert status["asOf"] == "2026-11-22T21:00:00+00:00"
    assert len(status["projectionOff"]) == len(status["projectionOn"]) == 24   # 6 h of 15 min
    # heating on ends warmer than heating off
    assert status["projectionOn"][-1]["temperature"] > status["projectionOff"][-1]["temperature"]


def test_forecast_is_hourly_and_within_the_weather_forecast(client_and_state):
    client, _ = client_and_state
    body = client.get("/api/family/margaret/forecast").get_json()
    hourly = body["hourly"]
    assert hourly and set(hourly[0]) == {"time", "predictedTemp"}
    times = [datetime.fromisoformat(p["time"]) for p in hourly]
    assert all((b - a).total_seconds() == 3600 for a, b in zip(times, times[1:]))
    assert times[-1] <= datetime.fromisoformat("2026-11-23T10:00:00+00:00")


def test_demo_bar_and_unknown_home(client_and_state):
    client, _ = client_and_state
    demo = client.get("/api/family/margaret/demo").get_json()
    assert demo["mode"] == "demo" and demo["clock"]["running"] is False
    assert client.get("/api/family/nobody/live-status").status_code == 404


def test_serves_the_app(client_and_state):
    client, _ = client_and_state
    assert b'<div id="app">' in client.get("/").data
    assert client.get("/js/app.js").status_code == 200


def test_tonight_and_home_list(client_and_state):
    """The evening's "tonight" card: Walter's bedroom is heading for about 9°C.
    In the day there's no card. /api/family/homes lists the demo homes."""
    client, state = client_and_state
    state.clock.jump_to(datetime.fromisoformat("2026-11-22T20:30:00+00:00"))
    walter = client.get("/api/family/walter/live-status").get_json()["tonight"]
    assert walter["predictedMin"] == pytest.approx(9.0, abs=0.5)
    assert walter["dangerFloor"] == 10.0 and not walter["assumedStart"]
    assert walter["curve"][0]["time"] < walter["predictedMinAt"] <= walter["curve"][-1]["time"]
    state.clock.jump_to(datetime.fromisoformat("2026-11-23T09:00:00+00:00"))
    assert client.get("/api/family/walter/live-status").get_json()["tonight"] is None
    homes = client.get("/api/family/homes").get_json()
    assert homes["mode"] == "demo"
    assert [h["home_id"] for h in homes["homes"]][-1] == "walter"
    assert all(h["name"] and h["story"] for h in homes["homes"])


def test_heating_on_projection_holds(client_and_state):
    """"If the heating stays on" never predicts a warm room getting warmer:
    it holds the room where it is, or warms it to 18.5°C."""
    client, state = client_and_state
    state.clock.jump_to(datetime.fromisoformat("2026-11-22T20:45:00+00:00"))
    walter = client.get("/api/family/walter/live-status").get_json()    # living room, heated
    on = [p["temperature"] for p in walter["projectionOn"]]
    assert max(on) - on[0] < 0.3
    state.clock.jump_to(datetime.fromisoformat("2026-11-22T23:30:00+00:00"))
    cold = client.get("/api/family/walter/live-status").get_json()      # bedroom, ~15°C
    on = [p["temperature"] for p in cold["projectionOn"]]
    assert on[0] < 16 and on[-1] == pytest.approx(18.5, abs=0.01)


def test_demo_notes(client_and_state):
    """The demo's notes: story beats so far plus Heatify's messages, oldest first."""
    client, state = client_and_state
    state.clock.jump_to(datetime.fromisoformat("2026-11-22T23:30:00+00:00"))
    notes = client.get("/api/family/margaret/demo").get_json()["notes"]
    texts = [n["text"] for n in notes]
    assert texts[0] == "Margaret's living room heating switches off."
    assert any("turns the heating back on" in t for t in texts)
    assert all(n["at"] <= "2026-11-22T23:30:00+00:00" for n in notes)
    assert [n["at"] for n in notes] == sorted(n["at"] for n in notes)
    joan = [text for _, text in state.demo.beats["joan"]]
    assert "Joan's boiler breaks down: no heating from now on." in joan
    assert not any("comes on" in t for t in joan)                        # not after the failure
