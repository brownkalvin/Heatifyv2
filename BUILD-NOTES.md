# Warmly build notes

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

- **Demo mode**: the six synthetic homes on a 720x clock that pauses whenever a
  message goes to the person. Reset with `POST /api/clock {"action": "reset"}`.
- **Live mode**: homes signed up with `POST /api/homes`, readings posted to
  `POST /api/reading` (ESP32, or the SwitchBot laptop gateway), real time. The
  forecast refreshes hourly (with the last 7 days, for learning) and every
  morning from 07:00 each home's heating habit and cooling rate are re-learned
  from the last 7 nights.
- **Texts**: set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM`
  (in `.env` is fine) and put numbers in `data/phones.json` (see
  `data/phones.example.json`), or give them at sign-up / in settings. Without
  Twilio, messages only appear in the on-screen log. Twilio replies: point the
  number's webhook at `https://<ngrok>/sms/reply`.
- **EPC lookup**: `EPC_API_KEY` in `.env`. Only look a home up with consent.

React: copy `contracts/warmly.ts` and `contracts/dashboard_sample.json` into
`web/src/`. `tests/test_server.py::test_contract_keys` fails if the JSON and the
TypeScript drift apart.

## Personal data (all gitignored)

`.env` (API keys), `data/homes.json`, `data/sensors.json`, `data/phones.json`,
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
- Margaret's daytime check-in fires at 21:00 on the fallback night; her bedtime
  message is skipped (rule 4) and the check-in carries the prediction.

**Messages and the API**
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
- `test_reanchor` checks that the re-anchored morning figure matches the real
  room (the doc's "lower than at 20:30" can't hold for the heating-off case).
- Amounts under 10p round to the nearest 1p ("about 3p"), as §4.4 now says.
- `predict_without_reading` takes optional `readings`; `heating_habit` takes no
  `home_id`.
- Real sensor readings without a UTC offset are rejected (§4.12).

## Not done

- Counting declines and acting on a "HELP" reply (the referral letter): backend.
- The React app; the SwitchBot and ESP32 gateways.
- Rotate the EPC key (it was pasted into a chat).
- `weatherapi.py` is the old pandas approach; nothing uses it.
