import initSqlJs from 'sql.js';
import { blankMetrics, mergeMetrics, metricKeys, metricValue } from './dashboard-data';
import type { Cache, Category, MetricKey, Metrics, Player, Segment, Session, View } from './dashboard-data';
import { atlantaDay, isDay, shiftDay, utcTimestamp } from './dates';

function rows(database: initSqlJs.Database, sql: string) {
  const result = database.exec(sql)[0];
  return result ? result.values.map(values => Object.fromEntries(
    result.columns.map((column, index) => [column, values[index]]),
  )) as Record<string, unknown>[] : [];
}
export function labelList(value: string): string[] {
  // Older caches stored Python representations of the label objects.
  const matches = [...value.matchAll(/['"]label['"]\s*:\s*['"]([^'"]+)['"]/g)].map(match => match[1]);
  return [...new Set((matches.length ? matches : value.split(',')).map(label => label.trim()).filter(Boolean))];
}
export function classifyLabels(labels: string[]): { category: Category; reason: string } {
  const normalized = labels.map(label => label.toLowerCase());
  const game = normalized.some(label => ['game', 'match'].includes(label));
  const practice = normalized.some(label => ['practice', 'training', 'shootaround'].includes(label));
  if (game && practice) return { category: 'other', reason: 'Mixed game and practice labels; requires review.' };
  if (game) return { category: 'game', reason: 'Game or Match source label. Not independently schedule-verified.' };
  if (practice) return { category: 'practice', reason: 'Practice, Training or Shootaround source label.' };
  return { category: 'other', reason: labels.length ? 'Unrecognized activity label; requires review.' : 'No activity label; requires review.' };
}
const sourceFields: Record<MetricKey, string[]> = {
  minutes: ['time_on_playing_field', 'duration'], distance_m: ['distance_total'],
  mechanical_load: ['mechanical_load'], physiological_load: ['physio_load'], accel_load: ['accel_load_accum'],
  metabolic_work: ['metabolic_work'], speed_max: ['speed_max'], jump_count: ['event_count_jump'],
  high_intensity_actions: ['event_count_acceleration', 'event_count_deceleration', 'event_count_change_of_direction'],
  acceleration_count: ['event_count_acceleration'], deceleration_count: ['event_count_deceleration'],
  change_of_direction_count: ['event_count_change_of_direction'],
  transition_count: ['event_count_full_court_transition', 'event_count_mid_court_transition'],
};
export function recordMetrics(record: Record<string, unknown>): Metrics {
  const result = blankMetrics();
  result.records = 1;
  let missing: string[] = [];
  if (typeof record.missing_fields === 'string') {
    missing = JSON.parse(record.missing_fields);
    if (!Array.isArray(missing) || missing.some(field => typeof field !== 'string')) throw new Error('Invalid missing-field metadata in cache.');
  }
  for (const key of metricKeys) {
    const fields = sourceFields[key];
    const absent = key === 'minutes' ? fields.every(field => missing.includes(field)) : fields.some(field => missing.includes(field));
    const value = Number(record[key]);
    if (absent || record[key] === null || record[key] === undefined || !Number.isFinite(value)) continue;
    result[key] = value;
    result.observed[key] = 1;
  }
  return result;
}
function groups() {
  return { all: blankMetrics(), game: blankMetrics(), practice: blankMetrics(), other: blankMetrics() };
}
export function readCache(database: initSqlJs.Database): Cache {
  const tables = new Set(rows(database, "SELECT name FROM sqlite_schema WHERE type='table'").map(row => String(row.name)));
  if (!['players', 'sessions', 'player_session_stats', 'sync_state'].every(table => tables.has(table))) {
    throw new Error('Not a VIPMBB SQLite cache. Choose the file created by the sync script.');
  }
  const players = new Map<number, Player>();
  for (const row of rows(database, 'SELECT * FROM players WHERE team_id = 3')) {
    const id = Number(row.player_id);
    players.set(id, { id, name: [row.first_name, row.last_name].filter(Boolean).join(' ') || `Player ${id}`,
      number: row.number == null ? '' : String(row.number), position: String(row.position || ''),
      active: !row.deleted, metrics: groups() });
  }
  const assignments = new Map<number, number[]>();
  if (tables.has('session_assignments')) for (const row of rows(database, 'SELECT * FROM session_assignments')) {
    const id = Number(row.session_id);
    assignments.set(id, [...(assignments.get(id) || []), Number(row.player_id)]);
  }
  const assignmentSync = new Set(tables.has('assignment_sync') ? rows(database, 'SELECT session_id FROM assignment_sync').map(row => Number(row.session_id)) : []);
  const sessions: Session[] = rows(database, 'SELECT * FROM sessions WHERE team_id = 3 ORDER BY start_session').map(row => {
    const labels = labelList(String(row.type_label || ''));
    const start = utcTimestamp(String(row.start_session));
    if (!Number.isFinite(start.getTime())) throw new Error('Cache contains an invalid session timestamp. Please synchronize it again.');
    const id = Number(row.session_id);
    return { id, date: atlantaDay(start), start: start.toISOString(), labels, ...classifyLabels(labels),
      metrics: blankMetrics(), playerIds: [], assignedIds: assignments.get(id) || [], assignmentsKnown: assignmentSync.has(id), zeroRecords: 0 };
  });
  const sync = rows(database, 'SELECT * FROM sync_state WHERE team_id = 3')[0];
  const meta = new Map(tables.has('cache_metadata') ? rows(database, 'SELECT * FROM cache_metadata').map(row => [String(row.key), String(row.value)]) : []);
  const records = rows(database, 'SELECT * FROM player_session_stats');
  // Validate before accepting the file so malformed uploads cannot crash rendering.
  records.forEach(recordMetrics);
  return { players, sessions, records,
    syncedAt: sync ? String(sync.last_completed_at) : null, checkedThrough: sync ? String(sync.range_end) : null,
    legacy: meta.get('sync_version') !== '2', syncStatus: meta.get('sync_status') || null };
}
let sqlReady: ReturnType<typeof initSqlJs> | undefined;
export async function openDashboardDatabase(buffer: ArrayBuffer): Promise<Cache> {
  if (buffer.byteLength > 100 * 1024 * 1024) throw new Error('Cache is larger than the 100 MB browser limit.');
  if (new TextDecoder().decode(buffer.slice(0, 15)) !== 'SQLite format 3') throw new Error('This is not a SQLite file.');
  sqlReady ??= initSqlJs({ locateFile: () => '/sql-wasm.wasm' }).catch(error => { sqlReady = undefined; throw error; });
  const SQL = await sqlReady;
  const database = new SQL.Database(new Uint8Array(buffer));
  try { return readCache(database); } finally { database.close(); }
}
export function queryDashboard(cache: Cache, start: string, end: string, segment: Segment = 'all',
  roster: 'participants' | 'active' = 'participants', playerId: number | null = null, metric: MetricKey = 'mechanical_load'): View {
  if (!isDay(start) || !isDay(end) || start > end) throw new Error('Choose a valid start and end date.');
  const dayCount = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  if (dayCount > 3660) throw new Error('Select a date range of 10 years or less.');
  const players = new Map([...cache.players].filter(([, player]) => roster !== 'active' || player.active)
    .filter(([id]) => playerId === null || playerId === id)
    .map(([id, player]) => [id, { ...player, metrics: groups() }]));
  const sessions = new Map(cache.sessions.filter(session => session.date >= start && session.date <= end)
    .map(session => [session.id, { ...session, metrics: blankMetrics(), playerIds: [] as number[], zeroRecords: 0 }]));
  for (const record of cache.records) {
    const session = sessions.get(Number(record.session_id));
    const player = players.get(Number(record.player_id));
    if (!session || !player) continue;
    const values = recordMetrics(record);
    mergeMetrics(player.metrics.all, values);
    mergeMetrics(player.metrics[session.category], values);
    mergeMetrics(session.metrics, values);
    session.playerIds.push(player.id);
    if (metricKeys.every(key => values[key] === 0)) session.zeroRecords++;
  }
  const scoped = [...sessions.values()].filter(session => playerId === null ||
    session.playerIds.includes(playerId) || session.assignedIds.includes(playerId));
  const selected = scoped.filter(session => segment === 'all' || segment === session.category);
  const totals = blankMetrics();
  const byCategory = { game: blankMetrics(), practice: blankMetrics(), other: blankMetrics() };
  for (const session of scoped) mergeMetrics(byCategory[session.category], session.metrics);
  for (const session of selected) mergeMetrics(totals, session.metrics);
  const days = Array.from({ length: dayCount }, (_, index) => {
    const date = shiftDay(start, index);
    const values = { date, game: 0 as number | null, practice: 0 as number | null, other: 0 as number | null };
    for (const category of ['game', 'practice', 'other'] as const) {
      const matches = selected.filter(session => session.date === date && session.category === category);
      if (!matches.length) continue;
      const metrics = blankMetrics();
      matches.forEach(session => mergeMetrics(metrics, session.metrics));
      values[category] = metricValue(metrics, metric);
    }
    return values;
  });
  return { players: [...players.values()].filter(player => roster === 'active' || player.metrics[segment].records > 0),
    sessions: selected.sort((a, b) => b.start.localeCompare(a.start)), totals, byCategory, days,
    withStats: selected.filter(session => session.metrics.records > 0).length,
    zeroRecords: selected.reduce((sum, session) => sum + session.zeroRecords, 0) };
}
