// Demo mode: replay a scripted cold evening (UI_SPEC.md §6).
// 18:00 heating on → 19:00 heating switched off → cools through Heads-up,
// Act now, Urgent → family member arrives and turns the heating on → Recovering → OK.
import { simulateTimeline, frameAt } from './mock.js';

const MEAN = 1; // cold evening outdoors
const HISTORY_STEPS = 24; // 6 h of history before the demo starts (12:00–18:00)
const DEMO_STEPS = 31; // 18:00 → 01:30

export function createDemo() {
  const start = new Date();
  start.setHours(12, 0, 0, 0);
  let rescued = false;
  let urgentReadings = 0;

  const recs = simulateTimeline({
    start,
    steps: HISTORY_STEPS + DEMO_STEPS,
    T0: 21,
    mean: MEAN,
    heating: (i, T, _recs, time) => {
      if (time.getHours() < 19) return true; // usual heating until 19:00
      if (rescued) return true;
      if (T < 16) urgentReadings++;
      if (urgentReadings >= 2) rescued = true; // they arrive and turn it on
      return rescued;
    },
  });

  return {
    length: DEMO_STEPS,
    frame: (k) => frameAt(recs, HISTORY_STEPS + k, MEAN),
  };
}
