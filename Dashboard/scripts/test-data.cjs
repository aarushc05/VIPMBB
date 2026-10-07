const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText, filename);
const { blankMetrics, metricKeys, mergeMetrics } = require('../lib/dashboard-data.ts');
const { atlantaDay, isDay, shiftDay } = require('../lib/dates.ts');
const { classifyLabels, labelList, queryDashboard, recordMetrics, readCache } = require('../lib/sqlite-dashboard.ts');
const initSqlJs = require('sql.js');
function player(id, active = true) {
  return { id, name: 'Test player ' + id, active, number: '', position: '',
    metrics: { all: blankMetrics(), game: blankMetrics(), practice: blankMetrics(), other: blankMetrics() } };
}
function session(id, date, category) {
  return { id, date, start: date + 'T20:00:00Z', category, labels: [], reason: '', metrics: blankMetrics(),
    playerIds: [], assignedIds: [1, 2], assignmentsKnown: true, zeroRecords: 0 };
}
function record(player_id, session_id, overrides = {}) {
  return { player_id, session_id, ...Object.fromEntries(metricKeys.map(key => [key, 10])), missing_fields: '[]', ...overrides };
}
function fixture() {
  return { players: new Map([[1, player(1)], [2, player(2, false)], [3, player(3)]]),
    sessions: [session(1, '2026-02-01', 'game'), session(2, '2026-02-02', 'practice'), session(3, '2026-02-03', 'other'), session(4, '2026-02-04', 'game')],
    records: [record(1, 1), record(2, 1, { distance_m: 20, speed_max: 12 }), record(1, 2), record(2, 3)], legacy: false, syncedAt: null, checkedThrough: '2026-02-04', syncStatus: 'complete' };
}
test('dates use Atlanta across midnight and DST', () => {
  assert.equal(atlantaDay(new Date('2026-02-12T02:00:00Z')), '2026-02-11');
  assert.equal(atlantaDay(new Date('2026-07-01T03:00:00Z')), '2026-06-30');
  assert.equal(shiftDay('2026-03-09', -2), '2026-03-07');
  assert.equal(isDay('2026-02-30'), false); assert.equal(isDay('2024-02-29'), true);
});
test('source label objects and mixed labels', () => {
  assert.deepEqual(labelList("{'label': 'Training', 'sortOrder': 1}, {'label': 'Match'}"), ['Training', 'Match']);
  assert.equal(classifyLabels(['Training', 'Match']).category, 'other');
  assert.equal(classifyLabels(['Match']).category, 'game');
  assert.equal(classifyLabels(['Training']).category, 'practice');
  assert.equal(classifyLabels(['Testing']).category, 'other');
  assert.equal(classifyLabels([]).category, 'other');
});
test('includes former participants; current-roster scope is explicit', () => {
  const cache = fixture();
  const all = queryDashboard(cache, '2026-02-01', '2026-02-04');
  assert.equal(all.players.length, 2); assert.equal(all.totals.distance_m, 50);
  const active = queryDashboard(cache, '2026-02-01', '2026-02-04', 'all', 'active');
  assert.equal(active.players.length, 2); assert.equal(active.totals.distance_m, 20);
  assert.equal(active.players.find(p => p.id === 3).metrics.all.records, 0);
});
test('calendar coverage includes sessions without player stats', () => {
  const view = queryDashboard(fixture(), '2026-02-01', '2026-02-04');
  assert.equal(view.sessions.length, 4); assert.equal(view.withStats, 3);
  assert.equal(view.days[3].game, null);
});
test('classification filters partition totals including review', () => {
  const cache = fixture();
  const views = ['all', 'game', 'practice', 'other'].map(segment => queryDashboard(cache, '2026-02-01', '2026-02-04', segment));
  assert.equal(views[0].totals.distance_m, views.slice(1).reduce((sum, view) => sum + view.totals.distance_m, 0));
  assert.equal(views[1].sessions.length, 2);
});
test('peak speed uses maximum, not sum', () => {
  const view = queryDashboard(fixture(), '2026-02-01', '2026-02-04', 'all', 'participants', null, 'speed_max');
  assert.equal(view.totals.speed_max, 12); assert.equal(view.days[0].game, 12);
});
test('missing fields differ from legitimate zero', () => {
  const values = recordMetrics(record(1, 1, { minutes: 0, distance_m: 0, missing_fields: '["distance_total","time_on_playing_field"]' }));
  assert.equal(values.observed.distance_m, 0); assert.equal(values.observed.minutes, 1); assert.equal(values.minutes, 0);
  const target = blankMetrics(); mergeMetrics(target, values); assert.equal(target.observed.distance_m, 0);
});
test('dates do not silently move to old records', () => {
  const view = queryDashboard(fixture(), '2026-09-24', '2026-09-30');
  assert.equal(view.sessions.length, 0); assert.equal(view.players.length, 0); assert.equal(view.days.length, 7);
  assert.equal(view.days[0].date, '2026-09-24'); assert.equal(view.days[6].date, '2026-09-30');
});
test('invalid ranges are rejected', () => {
  assert.throws(() => queryDashboard(fixture(), '2026-02-30', '2026-03-04'));
  assert.throws(() => queryDashboard(fixture(), '2026-03-04', '2026-02-01'));
  assert.throws(() => queryDashboard(fixture(), '2000-01-01', '2026-02-01'));
});
test('player focus includes assigned sessions even without measurements', () => {
  const view = queryDashboard(fixture(), '2026-02-01', '2026-02-04', 'all', 'participants', 2);
  assert.equal(view.players.length, 1); assert.equal(view.totals.distance_m, 30);
  assert.equal(view.sessions.length, 4); assert.equal(view.withStats, 2);
});
test('cached database totals reconcile exactly with SQLite', { skip: !process.env.KINEXON_TEST_DB }, async () => {
  const SQL = await initSqlJs();
  const db = new SQL.Database(fs.readFileSync(process.env.KINEXON_TEST_DB));
  try {
    const cache = readCache(db);
    const view = queryDashboard(cache, cache.sessions[0].date, cache.sessions.at(-1).date);
    const raw = db.exec('SELECT COUNT(*), SUM(distance_m), SUM(mechanical_load), MAX(speed_max) FROM player_session_stats')[0].values[0];
    assert.equal(view.totals.records, raw[0]);
    assert.ok(Math.abs(view.totals.distance_m - raw[1]) < 0.00001);
    assert.ok(Math.abs(view.totals.mechanical_load - raw[2]) < 0.00001);
    assert.equal(view.totals.speed_max, raw[3]);
    console.log(JSON.stringify({ sessions: view.sessions.length, withStats: view.withStats, playersWithStats: view.players.length,
      categories: Object.fromEntries(['game','practice','other'].map(type => [type, view.sessions.filter(s => s.category === type).length])),
      playerRecords: view.totals.records, firstDay: cache.sessions[0].date, lastDay: cache.sessions.at(-1).date }));
  } finally { db.close(); }
});
