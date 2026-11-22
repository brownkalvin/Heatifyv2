# UI Spec: Home Temperature Check-In App

> One-page spec agreed for the hackathon. Owner of the UI: Keira. Model owners: see §5 for what each API must return.

## 1. What it is

A mobile-first web app for **family members of an older person**. It watches the temperature in the older person's home and tells the family member **when they need to go round and turn the heating on**. The family member is solely responsible for turning the heating on, in person, at the house.

Winter only for v1: we alert on cold, not on overheating.

## 2. Screens

**Live panel (top of the app):**
- The current indoor temperature, its time, and whether the heating is on or off (as inferred by the physics model).
- A chart of the last 24 h of readings, with a dashed line at 18 °C.
- An alert card showing the current stage (§3).

**Week-ahead calendar (below the live panel):**
- A 7-day × 24-hour grid, with each cell coloured by predicted risk (§4).
- Each cold period carries a label: **"Be there by Tue 18:30"**.
- Tapping a day shows that day's predicted temperature curve.
- The calendar is **for planning only**. It never fires alerts. Alerts come only from live data.

## 3. Live alert stages

The UI calculates everything below from raw numbers (§5). "Crossing" means the first time the heating-off projection drops below 18 °C.

| Stage | Condition | Message (example) |
|---|---|---|
| OK | Heating on and ≥ 18 °C, or no crossing in the next 2 h | "Home is warm" |
| Heads-up | Heating off, crossing in 30 min to 2 h | "Be there by 21:45 to turn the heating on" |
| Act now | Heating off, crossing ≤ 30 min away, or temp now 16–18 °C | "Go now: it drops below 18 °C at 21:45" |
| Urgent | Heating off and temp < 16 °C | "Urgent: home is 15.2 °C and the heating is off" |
| Recovering | Heating on (inferred) and temp < 18 °C | "Heating is on, back to 18 °C by 22:30" |

- **Acknowledgement is automatic.** No button is needed. When the physics model reports `heatingOn: true`, the alert moves to Recovering. It clears to OK once the temperature is at or above 18 °C.
- **No buffer.** "Be there by" is the predicted crossing time itself.

## 4. Calendar risk bands

| Band | Predicted temp | Colour |
|---|---|---|
| OK | ≥ 18 °C | green / neutral |
| Cold | 16–18 °C | amber |
| Very cold | < 16 °C | red |

A **cold period** is a run of consecutive hours below 18 °C. Its "be there by" time is the interpolated time the forecast first crosses 18 °C.

> **For the predictive model owner:** the forecast should predict the home's **usual pattern**, including the occupant's normal heating habits as learned from past data. The calendar's job is to show when it is likely to get too cold anyway. (If it assumed the heating stays off for the whole week, every hour after the first night would show as cold.)

## 5. API contracts (Python services → UI)

All times are ISO 8601 with a timezone, e.g. `2026-11-22T21:45:00Z`. Temperatures are °C. **Enable FastAPI `CORSMiddleware`** (allow the web app's origin, or `*` for the hackathon), or the browser will block the calls.

```ts
// Thermostat API (simulated for now). A reading arrives roughly every 15 min.
GET /readings?since=<ISO>
→ [{ time: string, temperature: number }]

// Physics model: live status from the recent trajectory
GET /live-status
→ {
    heatingOn: boolean,                                       // inferred from the trajectory
    projectionOff: [{ time: string, temperature: number }],   // next ~6 h, 15-min steps, heating off
    projectionOn:  [{ time: string, temperature: number }]    // same horizon, heating on
  }

// Predictive model (it calls the weather API internally)
GET /forecast
→ { generatedAt: string, hourly: [{ time: string, predictedTemp: number }] }  // 168 entries, heating off
```

The UI polls `/readings` and `/live-status` every 60 s, and `/forecast` every 30 min. Base URLs live in one config file, so switching from mocks to the real APIs is a one-line change.

## 6. Demo mode

A toggle that replays a scripted cold evening in fast-forward (1 reading per second). It needs no backend. It walks through **OK → Heads-up → Act now → Urgent → (heating turned on) → Recovering → OK**, while the calendar shows a cold Thursday night. It doubles as a fallback if the live APIs fail during judging.

## 7. Stack and scope

- **Stack:** no build step. Preact + htm load from a CDN as plain ES modules, with a hand-built SVG chart and a CSS-grid calendar. The code is in `web/`.
- **Run it:** `cd web` then `python -m http.server 8000`, and open http://localhost:8000. Add `?demo` to the URL to start in demo mode.
- **Plug in the real APIs:** in `web/js/config.js`, set `useMock: false` and set the three URLs.
- **Mobile app later:** it has a web manifest now. It can be wrapped with Capacitor later with no rewrite.
- **Cut for v1:** setup screen (the house and heater are hard-coded), push notifications, calendar export, overheating alerts, travel-time buffer, and support for more than one home.

## 8. Plan (~3.5 h)

| Time | Work |
|---|---|
| 15 min | Scaffold, mock data, layout |
| 60 min | Live panel, alert stages, Recovering |
| 60 min | 7-day calendar |
| 45 min | Swap in the real APIs, fix bugs |
| 30 min | Polish, rehearse the demo |
