// Single place where the UI talks to the Python services (UI_SPEC.md §5).
import { CONFIG } from './config.js';
import * as mock from './mock.js';

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} returned HTTP ${res.status}`);
  return res.json();
}

/** Thermostat readings since a Date → [{ time, temperature }] */
export const fetchReadings = (since) =>
  CONFIG.useMock
    ? mock.readings(since)
    : getJSON(`${CONFIG.api.thermostat}/readings?since=${encodeURIComponent(since.toISOString())}`);

/** Physics model → { heatingOn, projectionOff, projectionOn } */
export const fetchLiveStatus = () =>
  CONFIG.useMock ? mock.liveStatus() : getJSON(`${CONFIG.api.physics}/live-status`);

/** Predictive model → { generatedAt, hourly: [{ time, predictedTemp }] } */
export const fetchForecast = () =>
  CONFIG.useMock ? mock.forecast() : getJSON(`${CONFIG.api.forecast}/forecast`);

// ---- Heatify's server: the demo clock and the person's replies ----

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || `${url} returned HTTP ${res.status}`);
  return json;
}

/** { mode, sim_time, clock: { running, paused_for_message, speed }, awaiting_reply, last_message } */
export const fetchServerDemo = () => getJSON(`${CONFIG.api.server}/api/family/${CONFIG.homeId}/demo`);

/** The homes the app can switch between → { mode, homes: [{ home_id, name, story }] } */
export const fetchHomes = () => getJSON(`${CONFIG.api.server}/api/family/homes`);

/** action: 'play' | 'pause' | 'resume' | 'reset' */
export const postClock = (action) => postJSON(`${CONFIG.api.server}/api/clock`, { action });

/** A reply from the person, as if they'd texted back. */
export const postReply = (text) => postJSON(`${CONFIG.api.server}/api/reply`, { home_id: CONFIG.homeId, text });

// ---- Sign-up (SignUpRequest / EpcLookupRequest in contracts/warmly.ts) ----

/** Sign a home up → SignUpResponse { home_id, live, notes, placement_text, ... } */
export const postSignUp = (body) => postJSON(`${CONFIG.api.server}/api/homes`, body);

/** The energy certificates at a postcode → { candidates, matching }. Only with consent. */
export const postLookup = (postcode, addressLine) =>
  postJSON(`${CONFIG.api.server}/api/signup/lookup`, { postcode, address_line: addressLine, consent: true });
