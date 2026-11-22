import { html, render, useState, useEffect, useMemo } from './preact.js';
import { CONFIG } from './config.js';
import { fetchReadings, fetchLiveStatus, fetchForecast, fetchServerDemo, fetchHomes, postClock, postReply } from './api.js';
import { normReadings, normStatus, normForecast, computeAlert, coldPeriods, bandFor } from './logic.js';
import { buildForecast } from './mock.js';
import { createDemo } from './demo.js';
import { SignUp } from './signup.js';
import { LineChart } from './chart.js';
import { Icon } from './icons.js';
import { fmtTime, fmtDay, fmtDayTime, fmtDuration, fmtTemp, dayKey } from './format.js';

const { ok: OK_T, urgent: URGENT_T } = CONFIG.thresholds;
let NAME = CONFIG.personName; // replaced by the home's name from the server (App)
const HOUR = 3600_000;

const STAGE = {
  ok: { label: 'All good', icon: Icon.check },
  headsup: { label: 'Heads-up', icon: Icon.clock },
  actnow: { label: 'Act now', icon: Icon.warning },
  urgent: { label: 'Urgent', icon: Icon.stop },
  recovering: { label: 'Warming up', icon: Icon.up },
};
const BAND_LABEL = { ok: 'OK', cold: 'Cold', verycold: 'Very cold' };

// ---------- data hooks ----------

function usePolling(enabled, load, intervalMs) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const run = async () => {
      try {
        const d = await load();
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    };
    run();
    const id = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [enabled]);
  return { data, error };
}

const loadLive = async () => {
  const [r, s] = await Promise.all([fetchReadings(new Date(Date.now() - 24 * HOUR)), fetchLiveStatus()]);
  return { readings: normReadings(r), status: normStatus(s) };
};
const loadForecast = async () => normForecast(await fetchForecast());

// ---------- Heatify server's demo clock (only with useMock: false) ----------

const QUICK_REPLIES = ['1', '2', 'HELP'];

function ServerBar() {
  const server = usePolling(!CONFIG.useMock, fetchServerDemo, CONFIG.pollLiveMs);
  const [override, setOverride] = useState(null); // the clock straight after a button press
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [text, setText] = useState('');
  useEffect(() => setOverride(null), [server.data]);

  const d = server.data;
  if (!d || d.mode !== 'demo') return null; // live mode runs on real time: nothing to control
  const clock = override ?? d.clock;
  const msg = d.last_message; // the last message to or from the person
  // A message to the person that hasn't been answered yet: show the reply box
  // (the clock keeps running; no reply in 30 minutes starts the next step)
  const waiting = Boolean(d.awaiting_reply && msg && msg.direction === 'out');

  const act = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (res.clock) setOverride(res.clock);
    } catch (e) {
      setError(e.message || String(e));
    }
    setBusy(false);
  };
  const reply = (t) => act(async () => { await postReply(t); setText(''); return {}; });

  return html`
    <section class="card demo-bar server-bar">
      <span class="footnote">
        ${`Demo night · ${fmtTime(new Date(d.sim_time))} · `}${clock.running ? 'running' : 'paused'}
      </span>
      <span class="server-buttons">
        ${clock.paused_for_message && !waiting &&
        html`<button class="reply-chip" disabled=${busy} onClick=${() => act(() => postClock('resume'))}>Continue</button>`}
        <button class="icon-button" disabled=${busy} aria-label="Back to 16:00" onClick=${() => act(() => postClock('reset'))}>
          ${Icon.replay(20)}
        </button>
        <button class="icon-button" disabled=${busy} aria-label=${clock.running ? 'Pause' : 'Play'}
          onClick=${() => act(() => postClock(clock.running ? 'pause' : 'play'))}>
          ${clock.running ? Icon.pause(20) : Icon.play(20)}
        </button>
      </span>
    </section>
    ${waiting &&
    html`<section class="card">
      <p class="footnote muted">Email to ${NAME} at ${fmtTime(new Date(msg.at))}</p>
      <p class="body">${msg.text}</p>
      <div class="reply-row">
        ${QUICK_REPLIES.map((r) => html`<button class="reply-chip" disabled=${busy} onClick=${() => reply(r)}>${r}</button>`)}
      </div>
      <form class="reply-row" onSubmit=${(e) => { e.preventDefault(); if (text.trim()) reply(text.trim()); }}>
        <input class="reply-input" value=${text} placeholder=${`Reply as ${NAME}…`}
          aria-label=${`Reply as ${NAME}`} onInput=${(e) => setText(e.currentTarget.value)} />
        <button class="reply-chip" type="submit" disabled=${busy || !text.trim()}>Send</button>
      </form>
    </section>`}
    ${(error || server.error) && html`<p class="footnote muted">${error || server.error}</p>`}
    ${d.notes && html`<${DemoNotes} notes=${d.notes} />`}
  `;
}

const NOTE_KIND = {
  event: { label: () => 'In the home', icon: Icon.house },
  person: { label: () => `Heatify → ${NAME}`, icon: Icon.info },
  family: { label: () => 'Heatify → family', icon: Icon.warning },
  reply: { label: () => `${NAME} replied`, icon: Icon.check },
};

/** The demo's running commentary: what's happened tonight, newest first. */
function DemoNotes({ notes }) {
  const [all, setAll] = useState(false);
  const newest = [...notes].reverse();
  const shown = all ? newest : newest.slice(0, 2);
  return html`
    <h3 class="section-header">What's happening</h3>
    <ol class="card notes" aria-live="polite">
      ${shown.length === 0 &&
      html`<li class="note"><p class="body muted">Nothing yet. Press play to start the evening.</p></li>`}
      ${shown.map((n, i) => {
        const k = NOTE_KIND[n.kind] || NOTE_KIND.event;
        return html`<li class=${'note note-' + n.kind + (i === 0 ? ' latest' : '')}>
          <span class="note-icon">${k.icon(18)}</span>
          <div class="note-text">
            <p class="footnote muted"><b>${fmtTime(new Date(n.at))}</b> · ${k.label()}</p>
            <p class="subhead">${n.text}</p>
            ${n.reason && html`<p class="footnote muted">Why: ${n.reason}</p>`}
          </div>
        </li>`;
      })}
    </ol>
    ${notes.length > 2 &&
    html`<button class="nav-button notes-more" onClick=${() => setAll(!all)}>
      ${all ? 'Show fewer' : `Show all ${notes.length}`}
    </button>`}
  `;
}

function useDemo(enabled) {
  const demo = useMemo(() => createDemo(), []);
  const [k, setK] = useState(0);
  const [playing, setPlaying] = useState(true);
  useEffect(() => {
    if (enabled) {
      setK(0);
      setPlaying(true);
    }
  }, [enabled]);
  useEffect(() => {
    if (!enabled || !playing) return undefined;
    const id = setInterval(() => setK((x) => Math.min(x + 1, demo.length - 1)), CONFIG.demo.stepMs);
    return () => clearInterval(id);
  }, [enabled, playing]);
  useEffect(() => {
    if (k >= demo.length - 1) setPlaying(false);
  }, [k]);
  const frame = useMemo(() => {
    if (!enabled) return null;
    const f = demo.frame(k);
    return { readings: normReadings(f.readings), status: normStatus(f.status) };
  }, [enabled, k]);
  return {
    frame,
    playing,
    done: k >= demo.length - 1,
    toggle: () => setPlaying((p) => !p),
    replay: () => {
      setK(0);
      setPlaying(true);
    },
  };
}

// ---------- components ----------

function alertText(alert, current, heatingOn) {
  switch (alert.stage) {
    case 'headsup':
      return [
        `Be at ${NAME}'s by ${fmtTime(alert.crossing)}`,
        `The home is cooling and will drop below ${OK_T} °C in about ${fmtDuration(alert.minutesLeft)}. Plan to go round and turn the heating on.`,
      ];
    case 'actnow':
      return alert.alreadyBelow
        ? [`Go to ${NAME}'s now`, `It's ${fmtTemp(current.temperature)} and the heating is off. Turn the heating on as soon as you can.`]
        : [
            `Go now: below ${OK_T} °C at ${fmtTime(alert.crossing)}`,
            `Only ${fmtDuration(alert.minutesLeft)} until the home drops below ${OK_T} °C. Leave now to turn the heating on.`,
          ];
    case 'urgent':
      return [
        `${fmtTemp(current.temperature)} and the heating is off`,
        `Below ${URGENT_T} °C, health risks rise for older people. Get to ${NAME} and turn the heating on now. If you can't get there, ask someone nearby to go.`,
      ];
    case 'recovering':
      return [
        'Heating is on',
        alert.recoveredBy
          ? `Back to ${OK_T} °C by about ${fmtTime(alert.recoveredBy)}.`
          : `It may not reach ${OK_T} °C in the next 6 hours. Check the heating is set high enough.`,
      ];
    default:
      return [
        'Home is warm',
        heatingOn
          ? 'The heating is on.'
          : alert.nextCrossing
            ? `Heating is off. Expected to drop below ${OK_T} °C around ${fmtTime(alert.nextCrossing)}. You'll get a heads-up 2 hours before.`
            : `Heating is off. Not expected to drop below ${OK_T} °C in the next 6 hours.`,
      ];
  }
}

function AlertCard({ alert, current, heatingOn }) {
  const [title, body] = alertText(alert, current, heatingOn);
  const s = STAGE[alert.stage];
  return html`
    <section class=${'card alert stage-' + alert.stage} role="status" aria-live="assertive">
      <div class="alert-head">
        <span class="alert-icon">${s.icon(22)}</span>
        <span class="eyebrow">${s.label}</span>
      </div>
      <h2 class="title2">${title}</h2>
      <p class="body">${body}</p>
    </section>
  `;
}

const TONIGHT = {
  verycold: { title: 'Very cold night ahead', icon: Icon.stop },
  cold: { title: 'Cold night ahead', icon: Icon.warning },
  cool: { title: 'A cool night ahead', icon: Icon.clock },
  ok: { title: 'Warm enough tonight', icon: Icon.check },
};

function tonightLevel(t) {
  if (t.predictedMin < t.dangerFloor) return 'verycold';
  if (t.predictedMin < URGENT_T) return 'cold';
  if (t.predictedMin < OK_T) return 'cool';
  return 'ok';
}

/** The bedroom tonight with the heating off: the model's prediction, before it happens. */
function TonightCard({ tonight }) {
  const level = tonightLevel(tonight);
  const low = fmtTemp(tonight.predictedMin);
  const when = fmtTime(tonight.predictedMinAt);
  const ifOff = tonight.heatingOn ? 'If the heating goes off now' : 'If the heating stays off';
  const advice = {
    verycold: `That's very cold, below ${tonight.dangerFloor} °C. Make sure the heating is on tonight.`,
    cold: `Below ${URGENT_T} °C, health risks rise for older people. Make sure the heating is on tonight.`,
    cool: `That's below the ${OK_T} °C recommended for older people.`,
    ok: '',
  }[level];
  const t = TONIGHT[level];
  const refLines = [
    { value: OK_T, cls: 'cold', label: `${OK_T}°` },
    { value: URGENT_T, cls: 'verycold', label: `${URGENT_T}°` },
  ];
  if (tonight.predictedMin < tonight.dangerFloor + 2) {
    refLines.push({ value: tonight.dangerFloor, cls: 'danger', label: `${tonight.dangerFloor}°` });
  }
  const curve = tonight.curve;
  return html`
    <h3 class="section-header">Tonight</h3>
    <section class=${'card tonight tonight-' + level} aria-live="polite">
      <div class="alert-head">
        <span class="alert-icon">${t.icon(22)}</span>
        <span class="eyebrow">Predicted</span>
      </div>
      <h2 class="title2">${t.title}</h2>
      <p class="body">
        ${level === 'ok'
          ? `${ifOff}, ${NAME}'s bedroom should stay at ${OK_T} °C or above tonight.`
          : `${ifOff}, ${NAME}'s bedroom will drop to about ${low} by ${when}. ${advice}`}
      </p>
      ${tonight.assumedStart &&
      html`<p class="footnote muted">An estimate: there's no recent reading from the bedroom.</p>`}
      ${curve.length > 1 &&
      html`<${LineChart}
        label=${`Predicted bedroom temperature tonight with the heating off, falling to ${low} by ${when}`}
        xMin=${curve[0].time}
        xMax=${curve[curve.length - 1].time}
        height=${170}
        series=${[{ points: curve, cls: 'projected' }]}
        refLines=${refLines}
      />`}
    </section>
  `;
}

function NowTab({ data, isDemo, error }) {
  const current = data.readings[data.readings.length - 1];
  const { heatingOn } = data.status;
  const alert = computeAlert({ current, ...data.status });
  const nowMs = data.status.asOf ? data.status.asOf.getTime() : Date.now(); // the server's clock if it sends one
  const stale = !isDemo && (nowMs - current.time) / 60_000 > CONFIG.staleAfterMin;
  const projection = heatingOn ? data.status.projectionOn : data.status.projectionOff;
  const band = bandFor(current.temperature);

  useEffect(() => {
    document.title = alert.stage === 'ok' ? CONFIG.appName : `(!) ${STAGE[alert.stage].label} · ${CONFIG.appName}`;
  }, [alert.stage]);

  return html`
    <${AlertCard} alert=${alert} current=${current} heatingOn=${heatingOn} />
    ${(stale || error) &&
    html`<section class="card notice">
      <span class="notice-icon">${Icon.info(20)}</span>
      <p class="footnote">
        ${error ? `Can't reach the data service (${error}). ` : ''}
        ${stale ? `No new reading since ${fmtTime(current.time)}. The thermostat may be offline.` : ''}
      </p>
    </section>`}

    ${data.status.tonight && html`<${TonightCard} tonight=${data.status.tonight} />`}

    <h3 class="section-header">Inside now</h3>
    <section class="card">
      <div class="now">
        <div class=${'temp-icon band-' + band}>${Icon.thermometer(28)}</div>
        <div class="now-main">
          <div class=${'big band-text-' + band}>${fmtTemp(current.temperature)}</div>
          <div class="footnote muted">${BAND_LABEL[band]} · updated ${fmtTime(current.time)}</div>
        </div>
        <div class=${'pill ' + (heatingOn ? 'on' : 'off')}>
          ${Icon.flame(16)}<span>Heating ${heatingOn ? 'on' : 'off'}</span>
        </div>
      </div>
    </section>

    <h3 class="section-header">Last 12 hours and next 6</h3>
    <section class="card">
      <${LineChart}
        label="Indoor temperature over the last 12 hours, with a projection for the next 6"
        xMin=${new Date(current.time - 12 * HOUR)}
        xMax=${new Date(current.time.getTime() + 6 * HOUR)}
        marker=${current.time}
        series=${[
          { points: data.readings, cls: 'actual' },
          { points: [current, ...projection], cls: 'projected' },
        ]}
        refLines=${[
          { value: OK_T, cls: 'cold', label: `${OK_T}°` },
          { value: URGENT_T, cls: 'verycold', label: `${URGENT_T}°` },
        ]}
      />
      <p class="legend footnote">
        <span class="key actual"></span> Measured
        <span class="key projected"></span> Projected if heating stays ${heatingOn ? 'on' : 'off'}
      </p>
    </section>

    <p class="footnote muted about">
      ${`Alerts use the latest thermostat reading and a physics model of how the home cools. ${OK_T} °C is the recommended minimum for older people; below ${URGENT_T} °C, health risks rise. Predictions are estimates. If in doubt, check in.`}
    </p>
  `;
}

function WeekTab({ forecast }) {
  const [sel, setSel] = useState(null);
  const { hourly } = forecast;
  const periods = useMemo(() => coldPeriods(hourly), [hourly]);

  const days = useMemo(() => {
    const map = new Map();
    for (const p of hourly) {
      const key = dayKey(p.time);
      if (!map.has(key)) {
        const date = new Date(p.time);
        date.setHours(0, 0, 0, 0);
        map.set(key, { key, date, cells: Array(24).fill(null) });
      }
      map.get(key).cells[p.time.getHours()] = p;
    }
    return [...map.values()];
  }, [hourly]);

  const markers = new Set(periods.map((p) => `${dayKey(p.beThereBy)}-${p.beThereBy.getHours()}`));
  const selDay = days.find((d) => d.key === sel);

  return html`
    <h3 class="section-header">Plan ahead</h3>
    ${periods.length === 0
      ? html`<section class="card"><p class="body">No cold periods predicted this week.</p></section>`
      : html`<ul class="card list">
          ${periods.map(
            (p) => html`<li>
              <button class="row" onClick=${() => setSel(dayKey(p.beThereBy))}>
                <span class=${'row-icon band-' + p.band}>${p.band === 'verycold' ? Icon.stop(20) : Icon.warning(20)}</span>
                <span class="row-text">
                  <span class="headline">Be there by ${fmtDayTime(p.beThereBy)}</span>
                  <span class="footnote muted">
                    ${BAND_LABEL[p.band]}, low of ${fmtTemp(p.low)}${p.end ? ` · warms up by ${fmtDayTime(p.end)}` : ''}
                  </span>
                </span>
                <span class="chev">${Icon.chevron(18)}</span>
              </button>
            </li>`,
          )}
        </ul>`}

    <h3 class="section-header">Predicted temperature by hour</h3>
    <section class="card">
      <div class="cal" role="table" aria-label="Predicted temperature by hour for the next 7 days">
        <div class="cal-row cal-head" role="row">
          <span></span>
          ${Array.from({ length: 24 }, (_, h) => html`<span class="hh">${h % 6 === 0 ? String(h).padStart(2, '0') : ''}</span>`)}
        </div>
        ${days.map(
          (d) => html`
            <button
              class=${'cal-row' + (sel === d.key ? ' sel' : '')}
              role="row"
              aria-pressed=${sel === d.key}
              aria-label=${`${fmtDay(d.date)}: show the temperature curve`}
              onClick=${() => setSel(sel === d.key ? null : d.key)}
            >
              <span class="dl">${fmtDay(d.date)}</span>
              ${d.cells.map(
                (c, h) => html`<span
                  class=${'cell ' + (c ? 'cell-' + bandFor(c.temperature) : 'empty')}
                  title=${c ? `${fmtDayTime(c.time)} · ${fmtTemp(c.temperature)} · ${BAND_LABEL[bandFor(c.temperature)]}` : ''}
                  >${markers.has(`${d.key}-${h}`) ? html`<i class="dot"></i>` : ''}</span
                >`,
              )}
            </button>
          `,
        )}
      </div>
      <p class="legend footnote">
        <span class="key cell-ok"></span> ${OK_T} °C+
        <span class="key cell-cold"></span> Cold
        <span class="key cell-verycold"></span> Very cold
        <span class="key-dot"><i class="dot"></i></span> Be there by
      </p>
    </section>

    ${selDay &&
    html`<h3 class="section-header">${fmtDay(selDay.date)}</h3>
      <section class="card">
        <${LineChart}
          label=${`Predicted temperature on ${fmtDay(selDay.date)}`}
          xMin=${selDay.date}
          xMax=${new Date(selDay.date.getTime() + 24 * HOUR)}
          height=${180}
          series=${[{ points: selDay.cells.filter(Boolean), cls: 'actual' }]}
          refLines=${[
            { value: OK_T, cls: 'cold', label: `${OK_T}°` },
            { value: URGENT_T, cls: 'verycold', label: `${URGENT_T}°` },
          ]}
        />
      </section>`}

    <p class="footnote muted about">
      Predicted from this home's past temperatures and the weather forecast. Tap a day or a reminder to see its curve.
    </p>
  `;
}

function TabBar({ tab, setTab }) {
  const items = [
    { id: 'now', label: 'Now', icon: Icon.house },
    { id: 'week', label: 'Week', icon: Icon.calendar },
  ];
  return html`<nav class="tabbar" role="tablist">
    ${items.map(
      (it) => html`<button
        role="tab"
        aria-selected=${tab === it.id}
        class=${'tab' + (tab === it.id ? ' active' : '')}
        onClick=${() => setTab(it.id)}
      >
        ${it.icon(26)}<span>${it.label}</span>
      </button>`,
    )}
  </nav>`;
}

/** The page in the URL's hash: '#signup' or the home's page. */
function useHashView() {
  const read = () => (location.hash === '#signup' ? 'signup' : 'home');
  const [view, setView] = useState(read);
  useEffect(() => {
    const onHash = () => { setView(read()); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (v) => { location.hash = v === 'signup' ? 'signup' : ''; };
  return [view, go];
}

/** The home's name from the server, so ?home=<id> shows the right person. */
function useHomeName(homeId) {
  const [name, setName] = useState(CONFIG.personName);
  useEffect(() => {
    if (CONFIG.useMock) return;
    fetchServerDemo().then((d) => d.name && setName(d.name)).catch(() => {});
  }, [homeId]);
  return name;
}

/** The homes to switch between: the demo's homes, or the signed-up ones. */
function useHomes() {
  const [homes, setHomes] = useState(null);
  useEffect(() => {
    if (CONFIG.useMock) return;
    fetchHomes().then(setHomes).catch(() => {});
  }, []);
  return homes;
}

/** One tab per home; in the demo, the chosen home's story underneath. */
function HomeTabs({ homes, homeId, onChoose }) {
  const current = homes.homes.find((h) => h.home_id === homeId);
  useEffect(() => {
    // Keep the chosen tab in view (Walter is at the end of a row that scrolls sideways)
    document.querySelector('.home-tab.active')?.scrollIntoView({ inline: 'center', block: 'nearest' });
  }, [homeId]);
  return html`<nav class="home-tabs" aria-label=${homes.mode === 'demo' ? 'Demo homes' : 'Homes'}>
    <div class="home-tabs-row" role="tablist">
      ${homes.homes.map(
        (h) => html`<button role="tab" aria-selected=${h.home_id === homeId}
          class=${'home-tab' + (h.home_id === homeId ? ' active' : '')} onClick=${() => onChoose(h.home_id)}>
          ${h.name}
        </button>`,
      )}
    </div>
    ${current && homes.mode === 'demo' && current.story && html`<p class="footnote muted home-story">${current.story}</p>`}
  </nav>`;
}

function App() {
  const [view, go] = useHashView();
  const [homeId, setHomeId] = useState(CONFIG.homeId);
  const homes = useHomes();
  const fallbackName = useHomeName(homeId);
  NAME = homes?.homes.find((h) => h.home_id === homeId)?.name || fallbackName;
  const choose = (id) => {
    CONFIG.homeId = id;
    const query = new URLSearchParams(location.search);
    query.set('home', id);
    history.replaceState(null, '', `?${query}${location.hash}`);
    setHomeId(id);
  };
  if (view === 'signup') {
    return html`
      <header class="nav">
        <div class="nav-bar">
          <button class="nav-button" onClick=${() => go('home')}>Back</button>
        </div>
        <h1 class="large-title">Sign up</h1>
      </header>
      <main><${SignUp} onClose=${() => go('home')} /></main>
    `;
  }
  return html`<${HomePage} key=${homeId} homes=${homes} homeId=${homeId} onChoose=${choose}
    onSignUp=${() => go('signup')} />`;
}

function HomePage({ homes, homeId, onChoose, onSignUp }) {
  // Open with ?demo to start straight in demo mode (handy for presenting).
  const [mode, setMode] = useState(() => (new URLSearchParams(location.search).has('demo') ? 'demo' : 'live'));
  const [tab, setTab] = useState(() => (new URLSearchParams(location.search).get('tab') === 'week' ? 'week' : 'now'));
  const isDemo = mode === 'demo';
  const live = usePolling(!isDemo, loadLive, CONFIG.pollLiveMs);
  const fc = usePolling(!isDemo, loadForecast, CONFIG.pollForecastMs);
  const demo = useDemo(isDemo);
  const demoForecast = useMemo(() => normForecast(buildForecast()), []);

  const data = isDemo ? demo.frame : live.data;
  const forecast = isDemo ? demoForecast : fc.data;
  const hasData = data && data.readings.length > 0;

  return html`
    <header class="nav">
      <div class="nav-bar">
        <button class="nav-button" onClick=${() => setMode(isDemo ? 'live' : 'demo')}>${isDemo ? 'Done' : 'Demo'}</button>
        ${!CONFIG.useMock && html`<button class="nav-button" onClick=${onSignUp}>Sign up</button>`}
      </div>
      <h1 class="large-title">${CONFIG.appName}</h1>
      <p class="subhead muted">${NAME}'s home</p>
    </header>
    ${!isDemo && homes && homes.homes.length > 1 &&
    html`<${HomeTabs} homes=${homes} homeId=${homeId} onChoose=${onChoose} />`}

    <main>
      ${!isDemo && !CONFIG.useMock && html`<${ServerBar} />`}
      ${isDemo &&
      html`<section class="card demo-bar">
        <span class="footnote">Demo: a cold evening, 15 minutes per second</span>
        <button
          class="icon-button"
          aria-label=${demo.done ? 'Replay' : demo.playing ? 'Pause' : 'Play'}
          onClick=${demo.done ? demo.replay : demo.toggle}
        >
          ${demo.done ? Icon.replay(20) : demo.playing ? Icon.pause(20) : Icon.play(20)}
        </button>
      </section>`}

      ${tab === 'now'
        ? hasData
          ? html`<${NowTab} data=${data} isDemo=${isDemo} error=${isDemo ? null : live.error} />`
          : html`<section class="card"><p class="body">${live.error ? `Can't reach the thermostat: ${live.error}` : 'Waiting for the first reading…'}</p></section>`
        : forecast
          ? html`<${WeekTab} forecast=${forecast} />`
          : html`<section class="card"><p class="body">${fc.error ? `Can't load the forecast: ${fc.error}` : 'Loading the week ahead…'}</p></section>`}
    </main>

    <${TabBar} tab=${tab} setTab=${setTab} />
  `;
}

render(html`<${App} />`, document.getElementById('app'));
