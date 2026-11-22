// Minimal SVG line chart (no chart library needed).
import { html } from './preact.js';
import { fmtTime } from './format.js';

export function LineChart({ series, xMin, xMax, refLines = [], marker, height = 220, label }) {
  const W = 600;
  const H = height;
  const m = { l: 38, r: 12, t: 12, b: 26 };
  const inRange = (p) => p.time >= xMin && p.time <= xMax;
  const shown = series.map((s) => ({ ...s, points: s.points.filter(inRange) }));
  const vals = shown.flatMap((s) => s.points.map((p) => p.temperature)).concat(refLines.map((r) => r.value));
  if (!vals.length) return null;

  const yMin = Math.floor(Math.min(...vals) - 0.5);
  const yMax = Math.ceil(Math.max(...vals) + 0.5);
  const sx = (t) => m.l + ((t - xMin) / (xMax - xMin)) * (W - m.l - m.r);
  const sy = (v) => m.t + ((yMax - v) / (yMax - yMin)) * (H - m.t - m.b);
  const path = (pts) =>
    pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.time).toFixed(1)},${sy(p.temperature).toFixed(1)}`).join('');

  const yStep = Math.max(1, Math.ceil((yMax - yMin) / 5));
  const yTicks = [];
  for (let v = Math.ceil(yMin / yStep) * yStep; v <= yMax; v += yStep) yTicks.push(v);

  const spanH = (xMax - xMin) / 3600_000;
  const xStep = spanH <= 12 ? 2 : spanH <= 24 ? 3 : 6;
  const xTicks = [];
  const t = new Date(xMin);
  t.setMinutes(0, 0, 0);
  while (t < xMin || t.getHours() % xStep) t.setHours(t.getHours() + 1);
  for (; t <= xMax; t.setHours(t.getHours() + xStep)) {
    const px = sx(t);
    if (px > m.l + 16 && px < W - m.r - 16) xTicks.push(new Date(t)); // skip labels that would clip
  }

  return html`
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label=${label}>
      ${yTicks.map(
        (v) => html`
          <line x1=${m.l} x2=${W - m.r} y1=${sy(v)} y2=${sy(v)} class="grid" />
          <text x=${m.l - 6} y=${sy(v) + 4} text-anchor="end" class="tick">${v}°</text>
        `,
      )}
      ${xTicks.map(
        (tt) => html`<text x=${sx(tt)} y=${H - 8} text-anchor="middle" class="tick">${fmtTime(tt)}</text>`,
      )}
      ${refLines.map(
        (r) => html`
          <line x1=${m.l} x2=${W - m.r} y1=${sy(r.value)} y2=${sy(r.value)} class=${'ref ' + r.cls} />
          <text x=${W - m.r} y=${sy(r.value) - 4} text-anchor="end" class=${'ref-label ' + r.cls}>${r.label}</text>
        `,
      )}
      ${marker &&
      html`<line x1=${sx(marker)} x2=${sx(marker)} y1=${m.t} y2=${H - m.b} class="now-line" />`}
      ${shown.map((s) => s.points.length > 1 && html`<path d=${path(s.points)} class=${'line ' + s.cls} />`)}
    </svg>
  `;
}
