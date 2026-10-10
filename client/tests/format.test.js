import test from 'node:test';
import assert from 'node:assert/strict';
import { appRoute, dateRange, dateLabel, jobDateRange, timeLabel, number, percentage, validRange, sortRows } from '../src/format.js';

test('presets use actual calendar days including today rather than the last activity', () => {
  assert.deepEqual(dateRange('week', '2026-10-07'), { start: '2026-10-01', end: '2026-10-07' });
  assert.deepEqual(dateRange('month', '2026-10-07'), { start: '2026-09-08', end: '2026-10-07' });
  assert.deepEqual(dateRange('all', '2026-10-07'), { start: '', end: '' });
});
test('calendar ranges survive DST, year rollover and leap years', () => {
  assert.deepEqual(dateRange('week', '2026-03-10'), { start: '2026-03-04', end: '2026-03-10' });
  assert.deepEqual(dateRange('week', '2026-01-03'), { start: '2025-12-28', end: '2026-01-03' });
  assert.deepEqual(dateRange('week', '2024-03-02'), { start: '2024-02-25', end: '2024-03-02' });
});
test('date-only labels do not shift to the previous day', () => assert.equal(dateLabel('2026-10-07'), 'Oct 7, 2026'));
test('timestamps are converted to Atlanta with implicit UTC for legacy values', () => {
  assert.equal(timeLabel('2026-03-05T01:00:00Z'), '8:00 PM');
  assert.equal(timeLabel('2026-03-05T01:00:00'), '8:00 PM');
});
test('missing measurements are not represented as zero', () => {
  assert.equal(number(null), '—'); assert.equal(number(undefined), '—'); assert.equal(number(''), '—');
  assert.equal(number(0), '0'); assert.equal(percentage(null), '—'); assert.equal(percentage(0), '0%');
});
test('reversed ranges are rejected', () => {
  assert.equal(validRange('2026-10-08', '2026-10-07'), false);
  assert.equal(validRange('2026-10-07', '2026-10-07'), true);
});
test('table sorting places missing values last in both directions without mutating input', () => {
  const rows = [{ id: 1, val: null }, { id: 2, val: 20 }, { id: 3, val: 10 }];
  assert.deepEqual(sortRows(rows, 'val', 'desc').map(r => r.id), [2, 3, 1]);
  assert.deepEqual(sortRows(rows, 'val', 'asc').map(r => r.id), [3, 2, 1]);
  assert.deepEqual(rows.map(r => r.id), [1, 2, 3]);
});
test('canonical document and report URLs resolve to their exact records', () => {
  assert.deepEqual(appRoute('/knowledge', '#document-1'), { page: 'knowledge', id: 1 });
  assert.deepEqual(appRoute('/reports/1786'), { page: 'report', id: 1786 });
  assert.deepEqual(appRoute('/', '#knowledge/8'), { page: 'knowledge', id: 8 });
  assert.deepEqual(appRoute('/reports/1786', '#assistant'), { page: 'assistant', id: null });
  assert.deepEqual(appRoute('/knowledge', '#bad-fragment'), { page: 'practices', id: null });
});
test('sync job ranges show explicit payload dates, resolved defaults or honest unspecified bounds', () => {
  assert.equal(jobDateRange({ kind: 'sync', payload: { start: '2026-01-01', end: '2026-01-31' } }), 'Jan 1, 2026 – Jan 31, 2026');
  assert.equal(jobDateRange({ kind: 'sync', payload: {}, result: { start: '2026-10-01', end: '2026-10-07' } }), 'Oct 1, 2026 – Oct 7, 2026');
  assert.equal(jobDateRange({ kind: 'sync', payload: {} }), 'Default sync range');
  assert.equal(jobDateRange({ kind: 'sync', payload: { end: '2026-10-07' } }), 'Default start – Oct 7, 2026');
  assert.equal(jobDateRange({ kind: 'report', payload: { session_id: 1786 } }), null);
});
