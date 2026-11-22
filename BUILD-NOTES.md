# Heatify build notes

How to run it, and where the build differs from `DESIGN-predictive-model.md`.
Update the design doc from the "Changes" section.

## Running it

```
pip install -r requirements.txt
pytest                                           # all tests, no network

python scripts/run_demo_night.py --offline       # the demo night in the terminal
python server.py                                 # demo server on :5000 (live forecast, cold night if mild)
WARMLY_FORECAST=offline python server.py         # demo server, no network needed
WARMLY_MODE=live python server.py                # real homes, real sensors, real time
```

- **Demo mode**: the seven synthetic homes on a 720x clock that keeps running when a
  message goes to the person. Reset with `POST /api/clock {"action": "reset"}`.
- **Live mode**: homes signed up with `POST /api/homes`, readings posted to
  `POST /api/reading` (ESP32, or the SwitchBot laptop gateway), real time. The
  forecast refreshes hourly (with the last 7 days, for learning) and every
  morning from 07:00 each home's heating habit and cooling rate are re-learned
  from the last 7 nights.
- **Emails** (how every message goes out; Twilio texts were removed): set `RESEND_API_KEY` in `.env` and put
  addresses in `data/emails.json`:
  `{"margaret": {"person": "...", "contact": "..."}}`. Without the key,
  messages only appear in the on-screen log. In demo mode nothing is sent until
  the clock is playing (the Play button in the app, or
  `POST /api/clock {"action": "play"}`); Margaret's first email is at 21:00. Without a
  verified domain Resend only delivers to the account's own email; set
  `RESEND_FROM` once a domain is verified. One-way: replies go through the
  on-screen phone. Sign-up and settings take `person_email` / `contact_email`
  (checked, then saved to `data/emails.json`; `""` in settings removes one).
- **EPC lookup**: `EPC_API_KEY` in `.env`. Only look a home up with consent.

**Family app** (`web/`, from CodeTheChange; spec in `web/UI_SPEC.md`): served by
`server.py` itself, so open http://localhost:5000. It shows one home
(`homeId` in `web/js/config.js`, default `margaret`) through
`/api/family/<home_id>/readings|live-status|forecast` (built in `family.py`),
following the room in use (living 07:00-22:00, bedroom at night). A bar at the
top drives the demo clock and lets you reply as the person. The week view
assumes the usual day (heated to 18°C 07:00-22:00, off overnight) and only
covers what the weather forecast covers (the offline/cold nights: ~18 h).
**Sign-up** is in the app too: "Sign up" at the top (or `/#signup`) is a form
for the family member (`web/js/signup.js` -> `POST /api/homes`): the person's
name, age, area and what the family calls them, the kind of home, the energy
rating (don't know / the band / look it up, with consent), the family
member's name and email, the person's email (optional) and the night-alert
opt-in. No thermometers yet. In live mode the new home's page is
`?home=<home_id>`; in demo mode it's saved to `data/homes.json` for live
mode and the demo is unchanged. `?home=` works for any home, e.g. `?home=frank`.
**Tonight card**: from 17:00 to 07:00 `/live-status` has `tonight` (the
bedroom's heating-off prediction, as in the bedtime message) and the Now tab
shows it with its curve: "Very cold night ahead" below 10°C, "Cold" below
16°C. **Home tabs**: `GET /api/family/homes` lists the homes; the app shows
a tab per home (the demo's seven, with each one's story) and switches
without reloading.
**Demo notes** ("What's happening", under the demo bar): what's happened
in the chosen home so far, newest first. Story beats from
`synthetic.demo_beats` (heating on and off, someone turning it back on, a
broken boiler, thermometers going quiet) plus every message Heatify sent or
got, with the reason it was sent (`/api/family/<id>/demo` -> `notes`).
**"If the heating stays on"** (`projectionOn`) holds the room where it is,
or warms it to 18.5°C if it's colder; it used to assume 21°C, so a warm room
was always shown getting warmer.
`useMock: true` in the config runs it with no server; its "Demo" button is
the built-in offline replay. `PORT=5055 python server.py` if 5000 is taken.

React: copy `contracts/warmly.ts` and `contracts/dashboard_sample.json` into
`web/src/`. `tests/test_server.py::test_contract_keys` fails if the JSON and the
TypeScript drift apart.

## Personal data (all gitignored)

`.env` (API keys), `data/emails.json`, `data/homes.json`, `data/sensors.json`, `data/phones.json`,
`data/readings.db`. The EPC test fixtures are a real certificate with the
address, postcode, town, property id and certificate number replaced.

## Changes from the design doc

**Demo homes and numbers**
- Eileen's bedroom is heated 16:00-20:00 (not 21:00), so her heating is seen to
  be off by bedtime: 17.8°C at 20:30, bedtime message at ~21:00 ("about 12°C"),
  11.6°C at 07:00.
- Synthetic readings start 90 minutes before 16:00 (in `api.start_demo`), so
  heating inference works from the first minute. Without it Frank's card
  warned at 17:00.
- Margaret's worked example checks 12.7°C at 07:00 with ±0.25 (seeded noise
  gives 12.9).
- Margaret's demo night ends differently: the first time the room in use
  falls to 16°C (the bedroom, about 22:30), someone turns her heating back on
  at 18.5°C and it's above 18°C within the hour (`synthetic.RESCUES`; her
  radiators warm at 3°C/h, not 1.5). Only the synthetic readings know about
  it; the model has to see it in the readings. So no morning message for her,
  and her night is labelled "heating on". The worked example above switches
  the rescue off.
- Margaret's daytime check-in fires at 21:00 on the fallback night; her bedtime
  message is skipped (rule 4) and the check-in carries the prediction.

- Walter (added): an old bungalow whose bedroom reaches about 9°C by 07:00
  with the heating off. `synthetic.tune_to_forecast` picks when his bedroom
  heating goes off (20:30, or earlier on a mild night) and its cooling time
  (15 h or more) so tonight's forecast gets him there; the model predicts
  with the same room. His family starts opted in to night alerts
  (`synthetic.DEMO_SETTINGS`, "Dad"). Offline forecast: the family's
  heads-up at 20:30, his bedtime message at 20:30, the night alert at 06:00.

**Messages and the API**
- The family's first ping, `family_heads_up`: as soon as the heating-off
  projection of the room in use goes below 16°C within 2 hours
  (`HEADS_UP_C`, `HEADS_UP_H`; `api.heads_up_crossing`). Not for a room
  already below 16°C, one whose heating is on, or one with no recent
  reading. At most once every 12 hours. Says the time and the bedroom's
  predicted overnight low.
- Extra templates: `checkin_reminder` (the 30-minute second text),
  `bedtime_estimate` (no reading, must say it's an estimate), `family_night`
  (opt-in night alert), `family_weekly_quiet` (weekly summary when there were
  no check-ins).
- `daytime_checkin_predicted` exists but is never sent: a check-in only fires
  once the room is already cold. The prediction is on the dashboard instead.
  **Decision needed** if it should be its own earlier message.
- The cost follow-up is only sent in the evening, and only if the heating can
  run (a broken boiler made it say "about nothing").
- The family step of the escalation may go out after 22:00; only the person is
  never texted at night.
- The weekly family summary goes out on Sundays from 17:00
  (`WEEKLY_SUMMARY_WEEKDAY`), counts only nights that had readings, and names
  the person ("Dad's bedroom below 18°C for 2+ hours on 5 of 7 nights...").
- `ConversationState` has extra fields: `last_template`, `replied_at`, `sent`,
  `replies`, `relationship_word`, `habit_on_nights`. `record_sent()` and
  `record_reply()` keep them up to date.
- `CostCard` has `blanket_tonight_p` and `whole_house_per_hour_p`.
- New API functions: `bedtime_options`, `learn_from_history`, `sign_up`,
  `load_demo_forecast`, `load_live_forecast`, `start_demo`, `export_demo_readings`.
- New endpoints: `POST /api/homes` (sign-up), `POST /api/homes/<id>/settings`,
  `POST /api/signup/lookup`. Types in `contracts/warmly.ts`.

**Model**
- `infer_heating` uses the doc's adaptive rule (90 minutes, 2·se); a held flat
  is caught ~94% of the time, pure cooling wrongly called on 0.4%.
  Plus a quicker rule first (`clear_rise`): warmer than outside, 0.5°C or more
  above the lowest reading of the last 90 minutes, and not falling over the
  last 30, means ON. Without it, heating coming back on after the room cooled
  (a V) read as off or "can't tell" for about an hour.
- `test_reanchor` checks that the re-anchored morning figure matches the real
  room (the doc's "lower than at 20:30" can't hold for the heating-off case).
- Amounts under 10p round to the nearest 1p ("about 3p"), as §4.4 now says.
- `predict_without_reading` takes optional `readings`; `heating_habit` takes no
  `home_id`.
- Real sensor readings without a UTC offset are rejected (§4.12).

## Not done

- Counting declines and acting on a "HELP" reply (the referral letter): backend.
- The React app; the SwitchBot and ESP32 gateways.
- Rotate the EPC key and the Resend key (both were pasted into a chat).
- `weatherapi.py` is the old pandas approach; nothing uses it.
