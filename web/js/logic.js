// Pure UI logic: turns raw model numbers into alert stages, risk bands and
// "be there by" times. No DOM, no fetching.
import { CONFIG } from './config.js';

const MIN = 60_000;

/** Raw API points ({ time: ISO, temperature | predictedTemp }) → { time: Date, temperature }. */
export function parseSeries(arr) {
  return (arr ?? []).map((p) => ({
    time: new Date(p.time),
    temperature: p.temperature ?? p.predictedTemp,
  }));
}

export const normReadings = (json) => parseSeries(json).sort((a, b) => a.time - b.time);

export const normStatus = (json) => ({
  heatingOn: Boolean(json.heatingOn),
  projectionOff: parseSeries(json.projectionOff),
  projectionOn: parseSeries(json.projectionOn),
  asOf: json.asOf ? new Date(json.asOf) : null, // the server's clock (the demo runs fast)
  tonight: json.tonight
    ? {
        predictedMin: json.tonight.predictedMin,
        predictedMinAt: new Date(json.tonight.predictedMinAt),
        assumedStart: Boolean(json.tonight.assumedStart),
        heatingOn: Boolean(json.tonight.heatingOn),
        dangerFloor: json.tonight.dangerFloor,
        curve: parseSeries(json.tonight.curve),
      }
    : null, // the bedroom tonight, 17:00-07:00 only
});

export const normForecast = (json) => ({
  generatedAt: new Date(json.generatedAt),
  hourly: parseSeries(json.hourly),
});

/**
 * First time a series crosses `threshold` (linear interpolation between points).
 * direction 'down': first time temperature < threshold.
 * direction 'up':   first time temperature >= threshold.
 * Returns the first point's time if it is already past the threshold, or null if it never crosses.
 */
export function findCrossing(series, threshold, direction) {
  if (!series.length) return null;
  const past = direction === 'down' ? (v) => v < threshold : (v) => v >= threshold;
  if (past(series[0].temperature)) return series[0].time;
  for (let i = 1; i < series.length; i++) {
    const a = series[i - 1];
    const b = series[i];
    if (past(b.temperature)) {
      const f = (threshold - a.temperature) / (b.temperature - a.temperature);
      return new Date(a.time.getTime() + f * (b.time - a.time));
    }
  }
  return null;
}

/**
 * Live alert stage from the latest reading and the physics model's status.
 * Stages: ok | headsup | actnow | urgent | recovering (see UI_SPEC.md §3).
 */
export function computeAlert({ current, heatingOn, projectionOff, projectionOn }) {
  const { ok, urgent } = CONFIG.thresholds;
  const now = current.time;
  const T = current.temperature;

  if (heatingOn) {
    if (T >= ok) return { stage: 'ok' };
    return { stage: 'recovering', recoveredBy: findCrossing([current, ...projectionOn], ok, 'up') };
  }
  if (T < urgent) return { stage: 'urgent' };
  if (T < ok) return { stage: 'actnow', alreadyBelow: true };

  const crossing = findCrossing([current, ...projectionOff], ok, 'down');
  if (!crossing) return { stage: 'ok' };
  const minutesLeft = (crossing - now) / MIN;
  if (minutesLeft <= CONFIG.stages.actNowMins) return { stage: 'actnow', crossing, minutesLeft };
  if (minutesLeft <= CONFIG.stages.headsUpMins) return { stage: 'headsup', crossing, minutesLeft };
  return { stage: 'ok', nextCrossing: crossing };
}

/** Calendar risk band for a predicted temperature. */
export function bandFor(temp) {
  const { ok, urgent } = CONFIG.thresholds;
  if (temp < urgent) return 'verycold';
  if (temp < ok) return 'cold';
  return 'ok';
}

/**
 * Runs of consecutive forecast hours below the OK threshold.
 * Each period: { beThereBy: Date, end: Date|null, low: number, band }.
 */
export function coldPeriods(hourly) {
  const { ok, urgent } = CONFIG.thresholds;
  const out = [];
  let i = 0;
  while (i < hourly.length) {
    if (hourly[i].temperature >= ok) {
      i++;
      continue;
    }
    const s = i;
    while (i < hourly.length && hourly[i].temperature < ok) i++;
    const low = Math.min(...hourly.slice(s, i).map((p) => p.temperature));
    out.push({
      beThereBy: s > 0 ? findCrossing([hourly[s - 1], hourly[s]], ok, 'down') : hourly[0].time,
      end: i < hourly.length ? findCrossing([hourly[i - 1], hourly[i]], ok, 'up') : null,
      low,
      band: low < urgent ? 'verycold' : 'cold',
    });
  }
  return out;
}
