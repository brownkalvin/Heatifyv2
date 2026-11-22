// App configuration. useMock: true runs on made-up data with no server at all.
// With useMock: false the app reads Heatify's server (python server.py), which
// also serves this page: open http://localhost:5000
// Which of the server's homes this app shows: ?home=<home_id> in the URL, or Margaret
const HOME_ID = new URLSearchParams(location.search).get('home') || 'margaret';

export const CONFIG = {
  appName: 'Heatify',
  personName: 'Margaret', // used in alert messages ("Be at Margaret's by 21:45"); the server's name wins
  homeId: HOME_ID,

  useMock: false,
  // Getters, so switching homes (the tabs) changes every URL at once
  api: {
    get thermostat() { return `/api/family/${CONFIG.homeId}`; }, // GET /readings?since=<ISO>
    get physics() { return `/api/family/${CONFIG.homeId}`; }, // GET /live-status
    get forecast() { return `/api/family/${CONFIG.homeId}`; }, // GET /forecast
    server: '', // Heatify's server (same origin): the demo clock and replies
  },

  // The server's demo clock runs at 720x (1 real second = 12 minutes), so poll often.
  pollLiveMs: 2_000, // readings + live status + demo clock
  pollForecastMs: 10_000, // week-ahead forecast
  staleAfterMin: 45, // warn if no reading for this long (readings arrive ~every 15 min)

  thresholds: { ok: 18, urgent: 16 }, // °C (the very-cold line, 10 °C, comes from the server)
  stages: { headsUpMins: 120, actNowMins: 30 },

  demo: { stepMs: 1000 }, // the built-in offline demo: one 15-minute reading per second
};
