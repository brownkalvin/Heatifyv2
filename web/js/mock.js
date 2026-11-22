// Mock versions of the three Python APIs. They return the exact JSON shapes
// in UI_SPEC.md §5, so the UI can't tell the difference.
// The house behaviour is a tiny Newton's-law-of-cooling sim, for mocks only.

const STEP_MIN = 15;
const HOUSE = { tauH: 6, gains: 2, heater: 22, setpoint: 21 };

let seed = 42;
const noise = () => {
  seed = (seed * 16807) % 2147483647;
  return (seed / 2147483647 - 0.5) * 0.1; // ±0.05 °C sensor noise
};
const r2 = (v) => Math.round(v * 100) / 100;
const addMin = (d, m) => new Date(d.getTime() + m * 60_000);

/** Outdoor temperature (°C): daily sinusoid peaking ~15:00. */
export function outdoorAt(t, mean) {
  const h = t.getHours() + t.getMinutes() / 60;
  return mean + 3 * Math.sin((2 * Math.PI * (h - 9)) / 24);
}

const expStep = (T, Teq, tauH, dtH) => Teq + (T - Teq) * Math.exp(-dtH / tauH);

/** Advance the house temperature by dtH hours. Heater on = thermostat holding the setpoint. */
export function stepHouse(T, Tout, heaterOn, dtH, house = HOUSE) {
  const off = Tout + house.gains;
  if (!heaterOn) return expStep(T, off, house.tauH, dtH);
  const n = expStep(T, off + house.heater, house.tauH, dtH);
  return n <= house.setpoint ? n : Math.max(house.setpoint, expStep(T, off, house.tauH, dtH));
}

/** 6 h projection in 15-min steps from (start, T0). */
export function project(start, T0, heaterOn, mean, steps = 24) {
  const out = [];
  let T = T0;
  let t = start;
  for (let i = 0; i < steps; i++) {
    T = stepHouse(T, outdoorAt(t, mean), heaterOn, STEP_MIN / 60);
    t = addMin(t, STEP_MIN);
    out.push({ time: t.toISOString(), temperature: r2(T) });
  }
  return out;
}

/**
 * Simulate 15-min readings. heating(i, T, records) decides whether the heater
 * runs during interval i → i+1. `reportedOn` is what the physics model would
 * infer at reading i (the interval that just ended).
 */
export function simulateTimeline({ start, steps, T0, mean, heating }) {
  const recs = [];
  let T = T0;
  for (let i = 0; i < steps; i++) {
    const time = addMin(start, i * STEP_MIN);
    const on = heating(i, T, recs, time);
    recs.push({
      time,
      T,
      on,
      reportedOn: i > 0 ? recs[i - 1].on : on,
      reading: r2(T + noise()),
    });
    T = stepHouse(T, outdoorAt(time, mean), on, STEP_MIN / 60);
  }
  return recs;
}

/** Build the API responses for "now = recs[k]". */
export function frameAt(recs, k, mean) {
  const cur = recs[k];
  return {
    readings: recs.slice(0, k + 1).map((r) => ({ time: r.time.toISOString(), temperature: r.reading })),
    status: {
      heatingOn: cur.reportedOn,
      projectionOff: project(cur.time, cur.reading, false, mean),
      projectionOn: project(cur.time, cur.reading, true, mean),
    },
  };
}

// ---- Mock "live" APIs: heating on all day, switched off ~75 min ago ----

const LIVE_MEAN = 3;
function liveTimeline() {
  const now = new Date();
  now.setMinutes(Math.floor(now.getMinutes() / STEP_MIN) * STEP_MIN, 0, 0);
  const steps = 97; // 24 h
  return simulateTimeline({
    start: addMin(now, -(steps - 1) * STEP_MIN),
    steps,
    T0: 21,
    mean: LIVE_MEAN,
    heating: (i) => i < steps - 6,
  });
}

export async function readings(since) {
  const recs = liveTimeline();
  return frameAt(recs, recs.length - 1, LIVE_MEAN).readings.filter((r) => new Date(r.time) >= since);
}

export async function liveStatus() {
  const recs = liveTimeline();
  return frameAt(recs, recs.length - 1, LIVE_MEAN).status;
}

// ---- Mock predictive model: 168 hourly predictions of the usual pattern ----
// The occupant heats 06:00–23:00; nights cool down. Day 4 is the cold snap.

const FORECAST_MEANS = [9, 8, 6, 2, -5, -4, 8, 8]; // outdoor daily mean by days from today
const FORECAST_HOUSE = { tauH: 30, gains: 3, heater: 25, setpoint: 20.5 };

export function buildForecast(from = new Date()) {
  const start = new Date(from);
  start.setMinutes(0, 0, 0);
  const day0 = new Date(start);
  day0.setHours(0, 0, 0, 0);
  const hourly = [];
  let T = 20.5;
  for (let i = 0; i < 168; i++) {
    const t = new Date(start.getTime() + i * 3600_000);
    hourly.push({ time: t.toISOString(), predictedTemp: r2(T) });
    const dayIdx = Math.round((new Date(t).setHours(0, 0, 0, 0) - day0) / 86_400_000);
    const mean = FORECAST_MEANS[Math.min(dayIdx, FORECAST_MEANS.length - 1)];
    const h = t.getHours();
    T = stepHouse(T, outdoorAt(t, mean), h >= 6 && h < 23, 1, FORECAST_HOUSE);
  }
  return { generatedAt: from.toISOString(), hourly };
}

export async function forecast() {
  return buildForecast();
}
