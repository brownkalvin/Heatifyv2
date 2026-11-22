# Heatify

Heatify watches how warm an older person's home is and warns their family
before it gets dangerously cold, so someone can go round and put the heating
on in time.

A thermometer in the living room and bedroom sends a reading every 15
minutes. A physics model of how the home cools, plus the weather forecast,
predicts where the temperature is heading. Heatify then:

- **pings the family 2 hours before** the room the person is in drops below
  16°C, if the heating stays off;
- checks in with the person by email ("Is your heating on?") and, at bedtime,
  tells them how cold their bedroom will get overnight and what it would cost
  to keep it warm;
- shows the family a web app with the temperature now, a prediction for
  tonight, and a week-ahead calendar of cold spells.

> The thresholds (18°C recommended minimum, 16°C, and the 10°C "very cold"
> line) are placeholders for the demo. They aren't health advice and still
> need agreeing with advisers.

## Quick start

You need Python 3.10 or newer.

```
pip install -r requirements.txt
python server.py
```

Then open **http://localhost:5000** (a phone-sized browser window looks best).

This starts the **demo**: seven made-up homes on a fast clock (1 second = 12
minutes), using tonight's real weather forecast, or last winter's coldest
night if tonight is too mild. To run with no internet at all:

```
# macOS / Linux
WARMLY_FORECAST=offline python server.py

# Windows PowerShell
$env:WARMLY_FORECAST="offline"; python server.py
```

If port 5000 is taken, set `PORT=5055` (or any free port) the same way.

## Running the demo

1. Pick a home with the **tabs** at the top. Each tab shows a one-line story of
   what happens in that home tonight.
2. Press **Play** in the demo bar. The evening starts at 16:00 and runs to
   10:00 the next morning in about 90 seconds. Pause and reset are next to it.
3. Watch **What's happening**: what happens in the home (heating going off,
   someone turning it back on) and every message Heatify sends, with the reason.
4. When a message goes to the person, a reply box appears. Reply as them with
   `1`, `2`, `HELP` or your own text. The clock keeps running; press Pause
   first if you want time to reply. No reply in 30 minutes starts the next
   step (a reminder, then the family).

The homes:

| Home | What happens |
|---|---|
| **Margaret** | Turns her heating off at 17:00 to save money. Gets a check-in, and the family is pinged. At 16°C someone turns the heating back on and the room is back above 18°C within the hour. |
| **Frank** | Heating on as normal. Nothing to warn about. |
| **Joan** | Boiler breaks down at 01:00. A slow drop and a morning check-in. |
| **Ray** | Thermometers stop sending readings at 19:00. Shows the "no reading" warning and estimates. |
| **Pat** | Warms the bedroom before bed: the cheaper middle way. |
| **Eileen** | Bedroom window left open, heating off at 20:00. Bedtime message: about 12°C by morning. |
| **Walter** | Old bungalow. His bedroom is predicted to fall to about **9°C** by morning: the family is pinged 2 hours before it drops below 16°C, he gets a bedtime message, and the family gets a night alert when it's below 10°C. |

The app's **Now** tab has the live alert, a **Tonight** card (the predicted
overnight low with the heating off, from 17:00) and a chart of the last 12
hours and the next 6. The **Week** tab is a planning calendar of predicted
cold spells.

The **Demo** button in the top corner is a separate, built-in replay that
needs no server.

You can also watch a demo night in the terminal:

```
python scripts/run_demo_night.py --offline
```

## Signing up a home

**Sign up** (top right, or http://localhost:5000/#signup) is for a family
member. It asks for the person's name, age, town and what you call them
("Mum"), the kind of home, its energy rating (don't know, the band, or look
it up on the public energy certificate register with their agreement), your
name and email, their email (optional), and whether you want alerts at night
too.

Signed-up homes are saved in `data/homes.json`. They appear in **live mode**:

```
WARMLY_MODE=live python server.py
```

Live mode runs on real time and needs real thermometers posting to
`POST /api/reading`. The thermometer gateways (SwitchBot, ESP32) aren't
written yet, so a signed-up home shows "Waiting for the first reading" for now.
To view one home directly: `http://localhost:5000/?home=<home_id>`.

## Emails (Resend)

Messages go out by email through [Resend](https://resend.com). Without it set
up, everything still works and messages only appear in the app.

1. Put your key in a `.env` file next to `server.py`:
   ```
   RESEND_API_KEY=re_...
   ```
2. Add addresses: at sign-up, or for the demo homes in `data/emails.json`:
   ```json
   {"margaret": {"person": "margaret@example.com", "contact": "family@example.com"}}
   ```
   `person` gets check-ins; `contact` (the family) gets the pings and alerts.
3. Until you verify a domain with Resend, it only delivers to your own Resend
   account's address. After verifying one, set `RESEND_FROM` (for example
   `Heatify <alerts@yourdomain.com>`).

Emails are one-way: replies to them aren't read yet.

## Settings

All optional, as environment variables or lines in `.env`:

| Setting | What it does |
|---|---|
| `WARMLY_MODE` | `demo` (default) or `live` |
| `WARMLY_FORECAST` | Demo weather: `live` (default, tonight's forecast), `offline` (built-in cold night), `cold` (last winter's coldest night) |
| `PORT` | Server port, default 5000 |
| `RESEND_API_KEY` | Turns on emails |
| `RESEND_FROM` | Sender, once you've verified a domain with Resend |
| `EPC_API_KEY` | Turns on the energy certificate lookup at sign-up |

The demo clock doesn't stop for messages. To make it wait for each reply
again, set `PAUSE_FOR_MESSAGES = True` near the top of `server.py`.

## Tests

```
pytest
```

The tests need no network and take a couple of seconds.

## How it's built

| Path | What's in it |
|---|---|
| `server.py` | The Flask server: demo clock, live mode, sending emails, the API. Also serves the web app. |
| `family.py` | The three endpoints the web app reads: readings, live status (with tonight's prediction) and the week forecast. |
| `store.py` | Saving signed-up homes and thermometers in `data/`. |
| `warmly/model/` | The model: `room_model.py` (how a room cools and warms), `predict.py`, `infer_heating.py` (is the heating on?), `rules.py` and `api.py` (when to send which message), `messages.py` (the wording), `cost.py`, `forecast.py` (Open-Meteo), `epc_lookup.py`, `synthetic.py` (the demo homes). |
| `warmly/sources/` | Where readings come from: the synthetic demo, or the database of real readings. |
| `web/` | The family web app: plain JavaScript with Preact and htm from a CDN, no build step. `web/UI_SPEC.md` is its spec. |
| `contracts/warmly.ts` | TypeScript types for the API. A test fails if they drift from the server. |
| `scripts/` | The terminal demo and an energy certificate API probe. |
| `BUILD-NOTES.md` | Developer notes: every place the build differs from the design, and why. |

The model in one paragraph: each room cools towards the outside temperature
at a rate set by how well it's insulated (worked out from the home's energy
rating, then learned from the thermometers). Heating warms the room at a
fixed rate, then holds it. Whether the heating is on is inferred from how
the readings move compared with how fast the room should be cooling. Every
prediction re-starts from the latest real reading.

(The code still uses the project's earlier working name, `warmly`, for the
Python package and settings.)

## Not done yet

- The thermometer gateways (SwitchBot and ESP32).
- A login: anyone who can reach the server can see and change every home.
- Acting on a "HELP" reply (help with bills), and counting "no thanks" replies.
- Reading replies to emails.
- A settings screen in the app (the server side exists).
- Hosting: it runs on Flask's development server.

## Personal data

`.env`, `data/homes.json`, `data/sensors.json`, `data/emails.json`,
`data/phones.json` and `data/readings.db` hold API keys or personal details.
They're in `.gitignore`: don't commit them.
