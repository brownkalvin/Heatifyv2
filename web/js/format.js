const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric' });
const weekdayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short' });

export const fmtTime = (d) => timeFmt.format(d); // "21:45"
export const fmtDay = (d) => dayFmt.format(d); // "Sun 22"
export const fmtDayTime = (d) => `${weekdayFmt.format(d)} ${timeFmt.format(d)}`; // "Tue 21:45"
export const fmtTemp = (v) => `${v.toFixed(1)} °C`;
export const dayKey = (d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

export function fmtDuration(mins) {
  const m = Math.max(0, Math.round(mins));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}`;
}
