"""Warmly's HTTP API for the React app (design doc §4.13).

    python server.py                            # demo: live forecast; last winter's coldest night if mild
    WARMLY_FORECAST=offline python server.py    # demo on the hard-coded cold night
    WARMLY_FORECAST=cold python server.py       # demo on last winter's coldest night
    WARMLY_MODE=live python server.py           # real homes, real sensors, real time

Two modes:
- demo: the six synthetic homes on a fast clock (720x) that pauses for replies.
- live: the homes signed up through POST /api/homes, their thermometers'
  readings from SQLite (posted to /api/reading), and the real time.

React can't call Python, so this small Flask server wraps warmly.model.api.
The server owns the shared state: the clock, the homes, the forecast, the
readings and each home's ConversationState. React only displays things and
sends button presses. Demo logs are in memory: restarting loses them.
"""
import json
import logging
import os
import sqlite3
import threading
import time as real_time
from contextlib import closing
from dataclasses import asdict, fields
from datetime import date, datetime, time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request

import store
from warmly.model import api
from warmly.model.epc_lookup import matches, search_epc
from warmly.sources import RealClock, StoredSource
from warmly.sources.stored_source import CREATE_TABLE

ALLOWED_ORIGINS = {"http://localhost:5173", "http://localhost:3000"}   # Vite, create-react-app
TICK_S = 0.5                       # the tick loop wakes this often (real seconds)
DB_PATH = Path(__file__).resolve().parent / "data" / "readings.db"
MILD_NIGHT_C = 2.0                 # a live forecast whose low is above this isn't worth demoing
FORECAST_REFRESH_S = 3600          # live mode refetches the forecast hourly
LEARN_AT = time(7, 0)              # live mode learns from last night every morning from 07:00

ENV_FILE = Path(__file__).resolve().parent / ".env"
# Real phone numbers per home (gitignored): {"margaret": {"person": "+44...", "contact": "+44..."}}
PHONES_FILE = Path(__file__).resolve().parent / "data" / "phones.json"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TWILIO_KEYS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")

# home_id -> {"person": number, "contact": number}. Loaded from PHONES_FILE at startup.
PHONES: dict[str, dict[str, str]] = {}
# The person's number -> home_id, so a real SMS reply finds its home (/sms/reply).
PHONE_TO_HOME: dict[str, str] = {}

log = logging.getLogger("warmly.server")


# --------------------------------------------------------------------------- #
# Settings files and sending texts
# --------------------------------------------------------------------------- #
def load_env_file(path: Path = ENV_FILE) -> None:
    """Copy KEY=value lines from .env into os.environ (real env vars win)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() and not name.lstrip().startswith("#"):
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def load_phones(path: Path = PHONES_FILE) -> None:
    """Fill PHONES and PHONE_TO_HOME from data/phones.json, if it exists."""
    try:
        phones = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    PHONES.clear()
    PHONES.update(phones)
    PHONE_TO_HOME.clear()
    PHONE_TO_HOME.update({n["person"]: home_id for home_id, n in phones.items() if n.get("person")})


def set_phones(home_id: str, person: str | None = None, contact: str | None = None,
               path: Path | None = None) -> dict[str, str]:
    """Change a home's numbers (from sign-up or settings) and save phones.json."""
    numbers = PHONES.setdefault(home_id, {})
    if person is not None:
        numbers["person"] = person
    if contact is not None:
        numbers["contact"] = contact
    PHONE_TO_HOME.clear()
    PHONE_TO_HOME.update({n["person"]: hid for hid, n in PHONES.items() if n.get("person")})
    path = path or PHONES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(PHONES, indent=2), encoding="utf-8")
    return numbers


def send_sms(message: api.OutgoingMessage) -> None:
    """Send a message by SMS if Twilio is set up and the home has a number for
    message.to; otherwise do nothing (the message is still in the on-screen
    log, so the demo works without it). Sends on a background thread, so a
    slow Twilio never holds up the tick loop."""
    if not all(os.environ.get(k) for k in TWILIO_KEYS):
        return
    to_number = PHONES.get(message.home_id, {}).get(message.to)
    if not to_number:
        log.info("No %s number for %s; not texting %s", message.to, message.home_id,
                 message.template_id)
        return
    threading.Thread(target=_post_to_twilio, args=(to_number, message.text), daemon=True).start()


def _post_to_twilio(to_number: str, text: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    try:
        response = requests.post(TWILIO_URL.format(sid=sid),
                                 auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]),
                                 data={"To": to_number, "From": os.environ["TWILIO_FROM"],
                                       "Body": text},
                                 timeout=10)
        if response.status_code >= 400:
            log.error("Twilio refused the SMS (%s): %s", response.status_code, response.text)
    except requests.RequestException:
        log.exception("Couldn't reach Twilio")


def choose_forecast(mode: str) -> tuple[list, str, object]:
    """(forecast, source, night_of) for the demo. In "live" mode, switches to
    last winter's coldest night if tonight is too mild to show anything."""
    if mode in ("offline", "cold"):
        return api.load_demo_forecast(mode)
    forecast, source, night_of = api.load_demo_forecast("live")
    demo = api.start_demo(forecast, night_of)
    tonight = [p.temp_c for p in forecast if demo.start <= p.timestamp <= demo.end]
    if source != "fallback" and (not tonight or min(tonight) > MILD_NIGHT_C):
        return api.load_demo_forecast("cold")
    return forecast, source, night_of


# --------------------------------------------------------------------------- #
# The server's state
# --------------------------------------------------------------------------- #
class WarmlyServer:
    """All the server's state, behind one lock.

    mode "demo": the synthetic homes and a SimClock (self.demo holds them).
    mode "live": signed-up homes, StoredSource readings and a RealClock.
    Tests can pass their own data_dir and clock, and turn off the forecast refresh.
    """

    def __init__(self, forecast: list, forecast_source: str, night_of: date | None = None,
                 mode: str = "demo", data_dir: Path = store.DATA_DIR, clock=None,
                 refresh_forecast: bool = True):
        self.mode = mode
        self.forecast = forecast
        self.forecast_source = forecast_source
        self.night_of = night_of or date.today()
        self.data_dir = Path(data_dir)
        self.live_clock = clock
        self.refresh_forecast = refresh_forecast and mode == "live"
        self.last_forecast_fetch = real_time.monotonic()
        self.lock = threading.RLock()
        self.settings: dict[str, dict] = {}       # demo homes' contact settings, kept across reset
        self.reset()

    @property
    def homes_file(self) -> Path:
        return self.data_dir / "homes.json"

    @property
    def sensors_file(self) -> Path:
        return self.data_dir / "sensors.json"

    # ---- state ----------------------------------------------------------- #
    def reset(self) -> None:
        """Demo: back to 16:00 with empty logs (same seed, so identical every
        time). Live: reload the signed-up homes and their sensors."""
        with self.lock:
            self.records = {r.home_id: r for r in store.load_records(self.homes_file)}
            if self.mode == "live":
                self.demo = None
                self.homes = [store.record_to_home(r) for r in self.records.values()]
                self.clock = self.live_clock or RealClock()
                self.source = StoredSource(DB_PATH, self.clock,
                                           store.load_sensor_list(self.sensors_file))
                self.end = None
            else:
                self.demo = api.start_demo(self.forecast, self.night_of)
                self.homes, self.clock = self.demo.homes, self.demo.clock
                self.source, self.end = self.demo.source, self.demo.end
            self.convo = {h.home_id: api.ConversationState(h.home_id) for h in self.homes}
            for home_id in self.convo:
                self.apply_settings(home_id)
            self.log = {h.home_id: [] for h in self.homes}
            self.outgoing: list[api.OutgoingMessage] = []
            self.running = self.mode == "live"
            self.paused_for_message = False
            self.last_learned: date | None = None

    def apply_settings(self, home_id: str) -> None:
        """Copy a home's contact settings into its ConversationState."""
        values = dict(self.settings.get(home_id, {}))
        if home_id in self.records:
            values.update({k: getattr(self.records[home_id], k) for k in store.SETTINGS_FIELDS})
        convo = self.convo[home_id]
        convo.contact_name = values.get("contact_name", "")
        convo.relationship_word = values.get("relationship_word", "")
        convo.night_alerts_opted_in = bool(values.get("night_alerts_opted_in", False))

    def now(self) -> datetime:
        """The current time. In the demo it stops at the end of the night (10:00)."""
        if self.end is not None and self.clock.now() >= self.end:
            self.clock.jump_to(self.end)
            self.clock.pause()
            self.running = False
        return self.clock.now()

    def clock_state(self) -> dict:
        return {"running": self.running, "paused_for_message": self.paused_for_message,
                "speed": self.clock.speed}

    def home(self, home_id: str):
        return next((h for h in self.homes if h.home_id == home_id), None)

    # ---- the tick loop ----------------------------------------------------- #
    def tick(self) -> None:
        """Send whatever's due. Called every TICK_S by the background thread.
        Live mode also refreshes the forecast hourly and learns every morning."""
        if (self.refresh_forecast
                and real_time.monotonic() - self.last_forecast_fetch > FORECAST_REFRESH_S):
            forecast, source = api.load_live_forecast()       # slow: outside the lock
            with self.lock:
                self.forecast, self.forecast_source = forecast, source
                self.last_forecast_fetch = real_time.monotonic()
        with self.lock:
            if not self.running or self.paused_for_message:
                return
            now = self.now()
            if self.mode == "live":
                self.learn(now)
            for message in api.pending_messages(now, self.homes, self.source,
                                                self.forecast, self.convo):
                self.send(message, now)

    def learn(self, now: datetime, force: bool = False) -> None:
        """Once a day from 07:00: each home's heating habit and fitted tau,
        worked out again from the last 7 nights of readings."""
        if not force and (now.time() < LEARN_AT or self.last_learned == now.date()):
            return
        for home in self.homes:
            learned = api.learn_from_history(home, now, self.source, self.forecast)
            home.rooms = learned.rooms
            self.convo[home.home_id].habit_on_nights = learned.habit_on_nights
        self.last_learned = now.date()

    def send(self, message: api.OutgoingMessage, now: datetime) -> None:
        """Log it, send it by SMS if Twilio is set up, and pause for person-facing ones."""
        api.record_sent(self.convo[message.home_id], message, now)
        self.outgoing.append(message)
        self.log[message.home_id].append({"at": now, "to": message.to,
                                          "template_id": message.template_id,
                                          "text": message.text, "direction": "out"})
        send_sms(message)
        if message.to == "person" and self.mode == "demo":
            self.clock.pause_for_message()
            self.paused_for_message = True

    def reply(self, home_id: str, text: str) -> None:
        """A reply from the person (on-screen phone or real SMS). Resumes the clock."""
        with self.lock:
            now = self.now()
            convo = self.convo[home_id]
            answered = convo.awaiting_reply or convo.last_template or ""
            api.record_reply(convo, text, now)
            self.log[home_id].append({"at": now, "to": "person", "template_id": answered,
                                      "text": text, "direction": "in"})
            self.continue_clock()

    def continue_clock(self) -> None:
        """Carry on after a message (a reply, or the operator pressing "continue",
        which counts as no reply)."""
        self.paused_for_message = False
        if self.running:
            self.clock.resume()

    def clock_action(self, action: str, to: str | None = None, speed: float | None = None) -> None:
        if self.mode == "live":
            raise ValueError("live mode runs on real time; the clock can't be changed")
        with self.lock:
            clock = self.clock
            if speed:
                clock.pause()
                clock.speed = float(speed)
            if action == "play":
                self.running = True
                if not self.paused_for_message:
                    clock.resume()
            elif action == "pause":
                self.running = False
                clock.pause()
            elif action == "resume":
                self.continue_clock()
            elif action == "reset":
                self.reset()
            elif action == "jump":
                when = datetime.fromisoformat(to)
                if when.tzinfo is None:
                    raise ValueError("'to' needs a UTC offset, e.g. 2026-11-22T20:30:00+00:00")
                clock.jump_to(when)
            else:
                raise ValueError(f"unknown action {action!r}")
            if self.running and not self.paused_for_message and action != "reset":
                clock.resume()

    # ---- sign-up and settings ----------------------------------------------- #
    def sign_up(self, body: dict) -> dict:
        """Create a home from the sign-up form. Raises ValueError on bad input.
        Saved to data/homes.json, data/sensors.json and data/phones.json; in
        live mode the home appears on the dashboard straight away."""
        form_fields = {f.name for f in fields(api.SignUp)}
        form = api.SignUp(**{k: v for k, v in body.items() if k in form_fields})
        with self.lock:
            result = api.sign_up(form, self.now().date())
            taken = set(self.records) | {h.home_id for h in self.homes}
            home_id, n = result.home.home_id, 2
            while home_id in taken:
                home_id, n = f"{result.home.home_id}-{n}", n + 1
            sensors = [{"id": str(s["id"]), "type": s["type"], "home_id": home_id,
                        "room": s["room"]} for s in body.get("sensors", [])]
            new_ids = {s["id"] for s in sensors}
            all_sensors = [s for s in store.load_sensor_list(self.sensors_file)
                           if s["id"] not in new_ids] + sensors
            store.save_sensor_list(self.sensors_file, all_sensors)   # checks them first
            record = store.HomeRecord(
                home_id=home_id, name=form.name, age=int(form.age), area=form.area,
                property_type=result.home.property_type, sap_score=result.home.sap_score,
                epc_band=result.home.epc_band, floor_area_m2=result.home.floor_area_m2,
                certificate_lodged=(result.certificate_lodged.isoformat()
                                    if result.certificate_lodged else None),
                **{k: body[k] for k in store.SETTINGS_FIELDS if k in body})
            self.records[home_id] = record
            store.save_records(self.homes_file, list(self.records.values()))
            if body.get("person_phone") or body.get("contact_phone"):
                set_phones(home_id, body.get("person_phone"), body.get("contact_phone"),
                           self.data_dir / "phones.json")
            if self.mode == "live":
                self.reset()
            return {"home_id": home_id, "rating_from": result.rating_from,
                    "certificate_is_old": result.certificate_is_old,
                    "placement_text": result.placement_text, "notes": result.notes,
                    "live": self.mode == "live", "record": asdict(record)}

    def update_settings(self, home_id: str, body: dict) -> dict:
        """Contact name, relationship word, the night-alert opt-in and phone numbers."""
        values = {k: body[k] for k in store.SETTINGS_FIELDS if k in body}
        if "night_alerts_opted_in" in values:
            values["night_alerts_opted_in"] = bool(values["night_alerts_opted_in"])
        with self.lock:
            if home_id in self.records:
                for key, value in values.items():
                    setattr(self.records[home_id], key, value)
                store.save_records(self.homes_file, list(self.records.values()))
                current = asdict(self.records[home_id])
            else:
                current = self.settings.setdefault(home_id, {})
                current.update(values)
            if "person_phone" in body or "contact_phone" in body:
                set_phones(home_id, body.get("person_phone"), body.get("contact_phone"),
                           self.data_dir / "phones.json")
            if home_id in self.convo:
                self.apply_settings(home_id)
            return {**{k: current.get(k) for k in store.SETTINGS_FIELDS},
                    "phones": dict(PHONES.get(home_id, {}))}


DemoServer = WarmlyServer   # the original name


def start_ticker(server: WarmlyServer) -> threading.Thread:
    """The background tick loop (a daemon thread, so Ctrl+C still stops the server)."""
    def loop() -> None:
        while True:
            real_time.sleep(TICK_S)
            try:
                server.tick()
            except Exception:   # keep the demo alive; the error is in the console
                log.exception("tick failed")
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def build_server_from_env() -> WarmlyServer:
    """WARMLY_MODE=demo|live and WARMLY_FORECAST=live|offline|cold."""
    if os.environ.get("WARMLY_MODE", "demo") == "live":
        forecast, source = api.load_live_forecast()
        server = WarmlyServer(forecast, source, mode="live")
        server.learn(server.now(), force=True)
        return server
    return WarmlyServer(*choose_forecast(os.environ.get("WARMLY_FORECAST", "live")))


# --------------------------------------------------------------------------- #
# The Flask app
# --------------------------------------------------------------------------- #
def create_app(server: WarmlyServer | None = None, ticker: bool = True) -> Flask:
    """The Flask app. Tests pass their own WarmlyServer and ticker=False."""
    server = server or build_server_from_env()
    app = Flask(__name__)
    app.config["warmly"] = server

    @app.after_request
    def cors(response: Response) -> Response:
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    def clock_json() -> dict:
        return {"sim_time": api.to_jsonable(server.now()), "clock": server.clock_state()}

    def bad(message: str, status: int = 400):
        return jsonify({"error": message}), status

    @app.get("/api/dashboard")
    def dashboard():
        with server.lock:
            now = server.now()
            homes = api.dashboard_state(now, server.homes, server.source, server.forecast)
            return jsonify({"api_version": api.API_VERSION, **clock_json(),
                            "homes": api.to_jsonable(homes)})

    @app.get("/api/homes/<home_id>")
    def home_detail(home_id: str):
        with server.lock:
            home = server.home(home_id)
            if home is None:
                return bad(f"no home {home_id!r}", 404)
            state = api.home_state(home, server.now(), server.source, server.forecast)
            return jsonify({**api.to_jsonable(state),
                            "messages": api.to_jsonable(server.log[home_id])})

    @app.post("/api/homes")
    def sign_up():
        """The sign-up form (see SignUpRequest in contracts/warmly.ts)."""
        body = request.get_json(silent=True) or {}
        if any(body.get(k) in (None, "") for k in ("name", "age", "area")):
            return bad("need name, age and area")
        try:
            return jsonify(server.sign_up(body)), 201
        except (ValueError, TypeError, KeyError) as error:
            return bad(str(error))

    @app.post("/api/homes/<home_id>/settings")
    def settings(home_id: str):
        if server.home(home_id) is None and home_id not in server.records:
            return bad(f"no home {home_id!r}", 404)
        return jsonify(server.update_settings(home_id, request.get_json(silent=True) or {}))

    @app.post("/api/signup/lookup")
    def signup_lookup():
        """The "pick your home" list from the energy-certificate register.
        Only with the person's (or their family's) consent."""
        body = request.get_json(silent=True) or {}
        if body.get("consent") is not True:
            return bad("the person (or their family) must agree to the lookup first")
        if not body.get("postcode"):
            return bad("need a postcode")
        candidates = [{k: c[k] for k in ("address", "certificate_number", "band", "registered")}
                      for c in search_epc(body["postcode"])]
        line = body.get("address_line", "")
        matching = [c for c in candidates if line and matches({"addressLine1": c["address"]}, line)]
        return jsonify({"candidates": candidates, "matching": matching})

    @app.get("/api/messages")
    def messages():
        with server.lock:
            return jsonify(api.to_jsonable(server.outgoing))

    @app.post("/api/clock")
    def clock():
        body = request.get_json(silent=True) or {}
        try:
            server.clock_action(body.get("action", ""), body.get("to"), body.get("speed"))
        except (ValueError, TypeError) as error:
            return bad(str(error))
        with server.lock:
            return jsonify(clock_json())

    @app.post("/api/reply")
    def reply():
        body = request.get_json(silent=True) or {}
        home_id, text = body.get("home_id"), body.get("text")
        if server.home(home_id) is None or not isinstance(text, str):
            return bad("need a known home_id and a text")
        server.reply(home_id, text)
        with server.lock:
            return jsonify({"home_id": home_id, "messages": api.to_jsonable(server.log[home_id])})

    @app.post("/sms/reply")
    def sms_reply():
        home_id = PHONE_TO_HOME.get(request.form.get("From", ""))
        if home_id and server.home(home_id):
            server.reply(home_id, request.form.get("Body", ""))
        return Response('<?xml version="1.0" encoding="UTF-8"?><Response/>', mimetype="text/xml")

    @app.post("/api/reading")
    def reading():
        """An ESP32 (or the SwitchBot laptop gateway) posts {"sensor_id", "temp_c", "ts"}."""
        body = request.get_json(silent=True) or {}
        try:
            when = datetime.fromisoformat(body["ts"])
            temp_c = float(body["temp_c"])
            sensor_id = str(body["sensor_id"])
        except (KeyError, TypeError, ValueError):
            return bad("need sensor_id, temp_c and an ISO-8601 ts")
        if when.tzinfo is None:
            return bad("ts needs a UTC offset")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(CREATE_TABLE)
            conn.execute("INSERT INTO readings VALUES (?, ?, ?)",
                         (sensor_id, when.isoformat(), temp_c))
            conn.commit()
        return "", 204

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "forecast_source": server.forecast_source})

    if ticker:
        start_ticker(server)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_env_file()
    load_phones()
    # debug=False: the reloader would start a second tick loop
    create_app().run(port=5000, threaded=True, debug=False)
