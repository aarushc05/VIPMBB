export const TIMEZONE = 'America/New_York';

export function dateLabel(value, options = {}) {
  if (!value) return 'Not available';
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00Z` : value);
  if (!Number.isFinite(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('en-US', { timeZone: TIMEZONE, month: 'short', day: 'numeric', year: 'numeric', ...options }).format(date);
}

export function timeLabel(value) {
  if (!value) return '—';
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-US', { timeZone: TIMEZONE, hour: 'numeric', minute: '2-digit' }).format(date) : '—';
}

export function timestampLabel(value) {
  if (!value) return 'Not yet';
  return `${dateLabel(value)} · ${timeLabel(value)} ET`;
}

export function number(value, digits = 0) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(Number(value));
}

export function percentage(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return `${Number(value) > 0 ? '+' : ''}${number(value, 1)}%`;
}

export function shiftDate(value, days) {
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

// Calendar-day windows include today; no fallback to the last recorded session.
export function dateRange(preset, today) {
  if (!today || !['week', 'month'].includes(preset)) return { start: '', end: '' };
  return { start: shiftDate(today, preset === 'week' ? -6 : -29), end: today };
}

export function validRange(start, end) {
  return !start || !end || start <= end;
}

export function sortRows(rows, key, direction = 'desc', getter = (row, field) => row[field]) {
  return [...rows].sort((a, b) => {
    const left = getter(a, key), right = getter(b, key);
    if (left === null || left === undefined || left === '') return right === null || right === undefined || right === '' ? 0 : 1;
    if (right === null || right === undefined || right === '') return -1;
    const result = typeof left === 'number' && typeof right === 'number' ? left - right : String(left).localeCompare(String(right), undefined, { numeric: true });
    return direction === 'asc' ? result : -result;
  });
}

export function sessionLabels(value) {
  if (!value) return 'No source label';
  if (Array.isArray(value)) return value.join(' · ') || 'No source label';
  return String(value);
}

export function appRoute(pathname = '/', hash = '') {
  const path = pathname.replace(/^\//, '').replace(/^reports\//, 'report/');
  const fragment = hash.replace(/^#\/?/, '');
  const document = /^document-(\d+)$/.exec(fragment);
  if (path === 'knowledge' && document) return { page: 'knowledge', id: Number(document[1]) };
  const [page, id] = (fragment || path || 'practices').split('/');
  if (['report', 'assistant', 'knowledge'].includes(page) && /^\d+$/.test(id)) return { page, id: Number(id) };
  return { page: ['practices', 'assistant', 'knowledge', 'system'].includes(page) ? page : 'practices', id: null };
}

export function jobDateRange(job) {
  if (job.kind !== 'sync') return null;
  const payload = job.payload || {};
  const start = payload.start || job.result?.start;
  const end = payload.end || job.result?.end;
  if (!start && !end) return 'Default sync range';
  return `${start ? dateLabel(start) : 'Default start'} – ${end ? dateLabel(end) : 'Default end'}`;
}
