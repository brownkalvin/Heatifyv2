// Simple line icons drawn for this app (24×24, stroke = currentColor).
import { html } from './preact.js';

const svg = (children, size = 24) => html`<svg
  width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
>${children}</svg>`;

export const Icon = {
  house: (s) => svg(html`<path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-6h4v6" />`, s),
  calendar: (s) => svg(html`<rect x="3" y="5" width="18" height="16" rx="3" /><path d="M3 10h18M8 3v4M16 3v4" />`, s),
  thermometer: (s) => svg(html`<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z" /><path d="M12 9v7" />`, s),
  flame: (s) => svg(html`<path d="M12 3c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-5 1-8.5z" />`, s),
  check: (s) => svg(html`<circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.5 2.5L16 9.5" />`, s),
  clock: (s) => svg(html`<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />`, s),
  warning: (s) => svg(html`<path d="M12 3l10 18H2L12 3z" /><path d="M12 10v5M12 18h.01" />`, s),
  stop: (s) => svg(html`<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z" /><path d="M12 8v5M12 16.5h.01" />`, s),
  up: (s) => svg(html`<circle cx="12" cy="12" r="9" /><path d="M12 16V8M8.5 11.5L12 8l3.5 3.5" />`, s),
  play: (s) => svg(html`<path d="M7 5l12 7-12 7z" />`, s),
  pause: (s) => svg(html`<path d="M8 5v14M16 5v14" />`, s),
  replay: (s) => svg(html`<path d="M4 12a8 8 0 1 0 3-6.2" /><path d="M4 4v5h5" />`, s),
  chevron: (s) => svg(html`<path d="M9 6l6 6-6 6" />`, s),
  info: (s) => svg(html`<circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />`, s),
};
