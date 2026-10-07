'use client';

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Activity, ArrowRight, CalendarDays, Database, FileUp, RefreshCw, Users } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { metricInfo, metricKeys, metricValue, formatMetric } from '@/lib/dashboard-data';
import type { Cache, Category, MetricKey, Metrics, Segment, Session } from '@/lib/dashboard-data';
import { openDashboardDatabase, queryDashboard, recordMetrics } from '@/lib/sqlite-dashboard';
import { atlantaDay, dateLabel, isDay, shiftDay, TIMEZONE } from '@/lib/dates';
import { schedule, scheduleStart, scheduleEnd, scheduleSource } from '@/lib/schedule';

const labels: Record<Segment, string> = { all: 'All activity', game: 'Game-labelled', practice: 'Practice', other: 'Needs review' };
const colors = { game: '#003057', practice: '#a38d43', other: '#d97736' };
const categories: Category[] = ['game', 'practice', 'other'];
const clockSubscribe = (notify: () => void) => {
  const timer = window.setInterval(notify, 60_000);
  return () => window.clearInterval(timer);
};
const emptyServerDay = () => '';
function display(metrics: Metrics, key: MetricKey) { return formatMetric(metricValue(metrics, key), key); }
function TypePill({ category }: { category: Category }) {
  return <span className={`type-pill type-${category}`}>{labels[category]}</span>;
}
function Summary({ title, value, detail }: { title: string; value: string; detail: string }) {
  return <div className="summary-card"><div className="eyebrow">{title}</div><div className="summary-value">{value}</div><p>{detail}</p></div>;
}

export default function Home() {
  // Server and initial hydration share the same placeholder. Dates use Atlanta,
  // independent of the browser/server timezone, and advance at local midnight.
  const today = useSyncExternalStore(clockSubscribe, atlantaDay, emptyServerDay);
  const [range, setRange] = useState<{ start: string; end: string } | null>(null);
  const [preset, setPreset] = useState<7 | 30 | null>(7);
  const [draftStart, setDraftStart] = useState('');
  const [draftEnd, setDraftEnd] = useState('');
  const [dateError, setDateError] = useState('');
  const [cache, setCache] = useState<Cache | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [source, setSource] = useState('');
  const [segment, setSegment] = useState<Segment>('all');
  const [metric, setMetric] = useState<MetricKey>('mechanical_load');
  const [roster, setRoster] = useState<'participants' | 'active'>('participants');
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const loadSequence = useRef(0);
  useEffect(() => {
    if (!today || preset === null) return;
    const next = { start: shiftDay(today, 1 - preset), end: today };
    setRange(next); setDraftStart(next.start); setDraftEnd(next.end);
  }, [today, preset]);
  useEffect(() => { void loadLocal(); return () => { loadSequence.current++; }; }, []);
  useEffect(() => { setPage(0); setExpanded(null); }, [range, segment, roster, playerId]);

  async function loadLocal() {
    const sequence = ++loadSequence.current;
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/data/kinexon.local.sqlite3', { cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 404 ? 'No local cache found. Run npm run sync, or open your SQLite file below.' : `Cache request failed (HTTP ${response.status}).`);
      const loaded = await openDashboardDatabase(await response.arrayBuffer());
      if (sequence !== loadSequence.current) return;
      setCache(loaded); setSource('Local SQLite cache');
    } catch (error) {
      if (sequence === loadSequence.current) setLoadError(error instanceof Error ? error.message : 'Could not load the cache.');
    } finally { if (sequence === loadSequence.current) setLoading(false); }
  }
  async function loadFile(file?: File) {
    if (!file) return;
    const sequence = ++loadSequence.current;
    setLoading(true); setLoadError('');
    try {
      if (file.size > 100 * 1024 * 1024) throw new Error('Choose a SQLite cache smaller than 100 MB.');
      const loaded = await openDashboardDatabase(await file.arrayBuffer());
      if (sequence !== loadSequence.current) return;
      setCache(loaded); setSource('Opened SQLite file');
    } catch (error) {
      if (sequence === loadSequence.current) setLoadError(error instanceof Error ? error.message : 'Invalid cache file.');
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }
  function applyRange(start: string, end: string, quick: 7 | 30 | null = null) {
    if (!isDay(start) || !isDay(end) || start > end) { setDateError('Enter valid dates, with the start on or before the end.'); return; }
    if ((Date.parse(end) - Date.parse(start)) / 86400000 > 3659) { setDateError('Choose a date range of 10 years or less.'); return; }
    setDateError(''); setRange({ start, end }); setDraftStart(start); setDraftEnd(end); setPreset(quick);
  }
  const view = useMemo(() => cache && range ? queryDashboard(cache, range.start, range.end, segment, roster, playerId, metric) : null,
    [cache, range, segment, roster, playerId, metric]);
  const overview = useMemo(() => cache && range ? queryDashboard(cache, range.start, range.end, 'all', roster, playerId, metric) : null,
    [cache, range, roster, playerId, metric]);
  const lastRecorded = cache?.sessions.at(-1)?.date;
  const firstRecorded = cache?.sessions[0]?.date;
  const partial = !!cache && (cache.legacy || (cache.syncStatus !== null && cache.syncStatus !== 'complete'));
  const stale = !!(range && cache && (!cache.checkedThrough || range.end > cache.checkedThrough));
  const players = [...(view?.players || [])].sort((a, b) =>
    (metricValue(b.metrics[segment], metric) ?? -1) - (metricValue(a.metrics[segment], metric) ?? -1));
  const chosen = metricInfo[metric];
  const chartData = view?.days.map(day => ({ ...day,
    ...Object.fromEntries(categories.map(category => [category, day[category] === null ? null : day[category]! * chosen.scale])) }));
  const focused = playerId === null ? null : cache?.players.get(playerId);
  const scheduled = range ? schedule.filter(game => game.date >= range.start && game.date <= range.end) : [];
  const matchedDates = scheduled.filter(game => cache?.sessions.some(session => session.date === game.date && session.category === 'game')).length;
  const selectedCategories = segment === 'all' ? categories : [segment];
  const recordedPlayers = view?.players.filter(player => player.metrics[segment].records > 0).length || 0;
  const pageSize = 20;
  const pageCount = Math.ceil((view?.sessions.length || 0) / pageSize);
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));
  const timeLabel = (stamp: string) => new Date(stamp).toLocaleString('en-US', { timeZone: TIMEZONE, dateStyle: 'medium', timeStyle: 'short' });

  function sessionDetails(session: Session) {
    const official = schedule.find(game => game.date === session.date);
    const records = cache!.records.filter(row => Number(row.session_id) === session.id &&
      (playerId === null || Number(row.player_id) === playerId) &&
      (roster !== 'active' || cache!.players.get(Number(row.player_id))?.active));
    const ids = new Set(records.map(row => Number(row.player_id)));
    const unrecorded = session.assignedIds.filter(id => !ids.has(id) && (playerId === null || id === playerId) &&
      (roster !== 'active' || cache!.players.get(id)?.active));
    return <div className="session-detail">
      <p><strong>Source:</strong> {session.labels.join(', ') || 'No label'} · {session.reason}</p>
      <p>{timeLabel(session.start)} Atlanta · {session.assignmentsKnown ? `${session.assignedIds.length} assigned players in the full session` : 'Assigned-player list unavailable in this cache'}</p>
      {official && <p className="warning-text">Official schedule on this date: {official.opponent} ({official.venue}). Date overlap does not verify this session as game data; the source classification is unchanged.</p>}
      {records.length > 0 && <div className="table-scroll"><table><thead><tr><th>Player</th><th>{chosen.label}</th><th>Distance (km)</th><th>Exposure (min)</th><th>Sensor quality</th></tr></thead><tbody>
        {records.map(row => {
          const values = recordMetrics(row);
          return <tr key={String(row.player_id)}><td>{cache!.players.get(Number(row.player_id))?.name || 'Unknown player'}</td>
            <td>{display(values, metric)}</td><td>{display(values, 'distance_m')}</td><td>{display(values, 'minutes')}</td>
            <td>{row.data_quality == null ? 'Not available' : `${Number(row.data_quality).toFixed(1)}%`}</td></tr>;
        })}
      </tbody></table></div>}
      {unrecorded.length > 0 && <p className="warning-text">Assigned without stats: {unrecorded.map(id => cache!.players.get(id)?.name || `Player ${id}`).join(', ')}.</p>}
      {!records.length && <p>No player statistics cached for this session and selection. This does not mean there was no activity.</p>}
    </div>;
  }

  return <main className="console">
    <header className="console-header"><div className="shell header-inner">
      <div className="brand"><div className="brand-mark">GT</div><div><p className="eyebrow">Georgia Tech · Men's Basketball</p><h1>McCamish performance</h1><p className="header-subtitle">Workload, participation & session context</p></div></div>
      <div className="header-status"><span className="status-dot" />{cache ? source : 'Local-first performance console'}<small>Private data stays in this browser</small></div>
    </div></header>
    <div className="shell content">
      <section className="intro"><div><p className="eyebrow">Performance overview</p><h2>See the work. Understand the context.</h2><p>Separate practice from game-labelled activity, then explore the players and sessions behind every total.</p></div>
        <div className="button-row"><button className="button secondary" onClick={() => void loadLocal()} disabled={loading}><RefreshCw size={15} />Reload cache</button>
          <button className="button primary" onClick={() => fileRef.current?.click()}><FileUp size={15} />Open SQLite file</button>
          <input ref={fileRef} type="file" accept=".sqlite3,.sqlite,.db" className="sr-only" aria-label="SQLite cache file" onChange={event => void loadFile(event.target.files?.[0])} /></div>
      </section>
      <section className="panel filters" aria-label="Dashboard filters">
        <div className="filter-row"><div className="quick-dates">
          <button className={`button ${preset === 7 ? 'primary' : 'secondary'}`} disabled={!today} onClick={() => applyRange(shiftDay(today, -6), today, 7)}>Last 7 days</button>
          <button className={`button ${preset === 30 ? 'primary' : 'secondary'}`} disabled={!today} onClick={() => applyRange(shiftDay(today, -29), today, 30)}>Last 30 days</button>
        </div><form className="date-form" onSubmit={event => { event.preventDefault(); applyRange(draftStart, draftEnd); }}>
          <label>Start date<input type="date" aria-label="Start date" value={draftStart} onChange={event => setDraftStart(event.target.value)} required /></label>
          <ArrowRight size={14} aria-hidden="true" /><label>End date<input type="date" aria-label="End date" value={draftEnd} onChange={event => setDraftEnd(event.target.value)} required /></label>
          <button className="button secondary" type="submit">Apply dates</button></form>
          <span className="timezone"><CalendarDays size={14} />Atlanta time</span></div>
        {dateError && <p className="warning-text" role="alert">{dateError}</p>}
        <div className="filter-row second-row"><div className="segment-control" aria-label="Activity type">{(['all', ...categories] as Segment[]).map(value =>
          <button key={value} aria-pressed={segment === value} className={segment === value ? 'selected' : ''} onClick={() => setSegment(value)}>{labels[value]}</button>)}</div>
          <label className="select-label">Roster<select aria-label="Roster scope" value={roster} onChange={event => { setRoster(event.target.value as typeof roster); setPlayerId(null); }}>
            <option value="participants">All historical participants</option><option value="active">Current active roster only</option></select></label>
          <label className="select-label">Metric<select aria-label="Performance metric" value={metric} onChange={event => setMetric(event.target.value as MetricKey)}>{metricKeys.map(key => <option key={key} value={key}>{metricInfo[key].label}</option>)}</select></label>
        </div>
      </section>
      {loading && <div className="notice" role="status"><Database size={18} />Loading and checking the SQLite cache…</div>}
      {loadError && <div className="notice warning" role="alert"><div><strong>Cache could not be loaded.</strong> {loadError}{cache && ' Your previously loaded cache is still displayed.'}<p>Files opened here are read locally and are not uploaded. The hosted dashboard does not contain player data.</p></div></div>}
      {cache && <section className="cache-strip" aria-label="Cache status"><span><Database size={14} />Last successful sync: <strong>{cache.syncedAt ? timeLabel(cache.syncedAt) : 'Unknown'}</strong></span>
        <span>Latest cached session: <strong>{lastRecorded ? dateLabel(lastRecorded, true) : 'None'}</strong></span>
        {lastRecorded && <button className="text-button" onClick={() => applyRange(shiftDay(lastRecorded, -29), lastRecorded)}>View latest recorded 30 days <ArrowRight size={13} /></button>}
        {firstRecorded && lastRecorded && <button className="text-button" onClick={() => applyRange(firstRecorded, lastRecorded)}>Full cached history</button>}
      </section>}
      {cache && (partial || stale) && <div className="notice warning"><div><strong>{partial ? 'Incomplete historical coverage.' : 'This date range is not fully synchronized.'}</strong>
        <p>{cache.legacy ? 'This older cache sampled the current roster only; former or assigned-only players may be missing, and missing fields were stored as zero. Run the updated sync to backfill history. ' : cache.syncStatus === 'incomplete' ? 'The last sync did not finish. Some records may have updated, but the successful checkpoint has not advanced. ' : ''}
          {stale ? `The last successful sync checked through ${cache.checkedThrough ? dateLabel(cache.checkedThrough, true) : 'an unknown date'}. An empty period is not proof of no activity.` : 'Totals describe cached records, not guaranteed complete team workload.'}</p></div></div>}
      <section className="range-heading"><div><p className="eyebrow">{focused ? 'Player focus' : 'Team view'} · {labels[segment]}</p><h2>{range ? `${dateLabel(range.start, range.start.slice(0, 4) !== range.end.slice(0, 4))} – ${dateLabel(range.end, true)}` : 'Preparing dates…'}</h2>
        {focused && <p>{focused.name} <button className="text-button" onClick={() => setPlayerId(null)}>Clear player filter</button></p>}</div>
        <span className="muted">All historical participants includes former players.</span></section>
      {!cache && !loading && <section className="panel empty"><Database size={32} /><h3>Connect your performance cache</h3><p>On your computer, add credentials to the private .env file and run <code>npm run dev</code>. This page will load your SQLite cache automatically.</p><p>On the hosted page, use “Open SQLite file”. No credentials or athlete records are included in the website.</p></section>}
      {view && cache && <>
        <section className="summary-grid">
          <Summary title="Players with records" value={String(recordedPlayers)} detail={roster === 'active' ? `of ${view.players.length} current roster players in this view` : 'Historical participants included'} />
          <Summary title="Session coverage" value={`${view.withStats} / ${view.sessions.length}`} detail="Cached sessions with player statistics" />
          <Summary title="Recorded distance" value={display(view.totals, 'distance_m')} detail="km · summed across recorded players" />
          <Summary title={chosen.label} value={display(view.totals, metric)} detail={`${chosen.unit} · ${metric === 'speed_max' ? 'maximum, never summed' : 'recorded total'}`} />
        </section>
        <section className="category-grid" aria-label="Game and practice comparison">{categories.map(category => {
          const sessions = overview!.sessions.filter(session => session.category === category);
          const values = view.byCategory[category];
          return <button key={category} className={`panel category-card ${segment === category ? 'active-category' : ''}`} onClick={() => setSegment(category)}>
            <div><TypePill category={category} /><span className="category-count">{sessions.length}<small> sessions</small></span></div>
            <strong>{display(values, metric)} <small>{chosen.unit}</small></strong>
            <p>{values.observed[metric]} measured player-session records{metric !== 'speed_max' && values.observed[metric] > 0 ? ` · ${formatMetric(values[metric] / values.observed[metric], metric)} average / record` : ''}</p>
          </button>;
        })}</section>
        <p className="classification-note">“Game-labelled” means Kinexon labels Game or Match, not a verified official contest. Mixed labels, testing and unlabeled sessions stay in Needs review. Multiple sessions can belong to one game.</p>
        {scheduled.length > 0 && <details className="panel schedule-panel"><summary><strong>Official schedule cross-check</strong><span>{scheduled.length} scheduled games · {scheduled.filter(game => game.venue === 'home').length} home · {matchedDates} dates with a game-labelled session</span><small>Expand date-by-date comparison</small></summary>
          <div className="schedule-explainer"><p>Team-level calendar comparison, independent of the player and activity filters. {scheduled.length - matchedDates} scheduled dates have no game-labelled session in this cache. Away games may be untracked; home-date mismatches may indicate missing sessions or incorrect source labels. Do not infer which explanation applies.</p>
            <p>Reference: <a href={scheduleSource} target="_blank" rel="noreferrer">GT Athletics 2025–26 schedule</a>, verified September 30, 2026. Covers {dateLabel(scheduleStart, true)}–{dateLabel(scheduleEnd, true)} only; other seasons are not checked.</p></div>
          <div className="table-scroll"><table><thead><tr><th>Official game date</th><th>Opponent</th><th>Venue</th><th>Game-labelled sessions</th><th>Practice / review sessions</th></tr></thead><tbody>{scheduled.map(game => {
            const sessions = cache.sessions.filter(session => session.date === game.date);
            return <tr key={game.date}><td>{dateLabel(game.date, true)}</td><td>{game.opponent}</td><td>{game.venue}</td>
              <td>{sessions.filter(session => session.category === 'game').length || 'None cached'}</td>
              <td>{sessions.filter(session => session.category === 'practice').length} / {sessions.filter(session => session.category === 'other').length}</td></tr>;
          })}</tbody></table></div></details>}
        {view.sessions.length === 0 ? <section className="panel empty"><CalendarDays size={32} /><h3>No cached sessions in this selection</h3><p>{stale ? 'Refresh the cache to verify recent activity, or explicitly select an earlier period.' : 'Try another activity type or date range. We never move “Last 7 days” or “Last 30 days” back to old activity.'}</p></section>
          : <section className="panel chart-panel"><div className="section-heading"><div><h3>{chosen.label} over time</h3><p>{focused ? focused.name : 'Selected roster'} · {metric === 'speed_max' ? 'daily maximum' : 'daily recorded totals'} · {chosen.unit}</p></div>
            <div className="legend">{selectedCategories.map(category => <span key={category}><i style={{ background: colors[category] }} />{labels[category]}</span>)}</div></div>
            {view.totals.observed[metric] > 0 ? <div className="trend-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 800, height: 280 }}>
              <BarChart data={chartData} accessibilityLayer margin={{ top: 10, right: 12, left: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="#e8edf0" /><XAxis dataKey="date" tickFormatter={value => dateLabel(value, range?.start.slice(0, 4) !== range?.end.slice(0, 4))} minTickGap={32} tickLine={false} axisLine={false} fontSize={11} />
                <YAxis tickLine={false} axisLine={false} fontSize={11} width={54} tickFormatter={value => Number(value).toLocaleString('en-US', { notation: 'compact' })} />
                <Tooltip labelFormatter={value => dateLabel(String(value), true)} formatter={(value, name) => [`${Number(value).toLocaleString('en-US', { maximumFractionDigits: chosen.decimals })} ${chosen.unit}`, labels[name as Category]]} />
                {selectedCategories.map(category => <Bar key={category} dataKey={category} stackId={metric === 'speed_max' ? undefined : 'activity'} fill={colors[category]} maxBarSize={40} isAnimationActive={false} />)}
              </BarChart></ResponsiveContainer></div> : <div className="empty compact"><Activity size={28} /><h3>No measurements for this metric</h3><p>Sessions exist, but no usable {chosen.label.toLowerCase()} values are cached for this selection.</p></div>}
            <p className="chart-note">Blank bars can mean no cached session or missing statistics; use the session log below to distinguish them. Partial coverage can understate totals.</p>
          </section>}
        <section className="panel"><div className="section-heading"><div><h3><Users size={18} />Player breakdown</h3><p>Sorted by {chosen.label.toLowerCase()}. Select a player to focus the chart and session log.</p></div>{focused && <button className="button secondary" onClick={() => setPlayerId(null)}>Show all players</button>}</div>
          {players.length ? <div className="table-scroll"><table><thead><tr><th>Player</th><th>Records</th><th>{chosen.label}<small>{chosen.unit}</small></th><th>Distance<small>km</small></th><th>Exposure<small>min</small></th><th>Peak speed<small>m/s</small></th><th>Measured coverage<small>selected metric</small></th></tr></thead>
            <tbody>{players.map(player => { const values = player.metrics[segment]; return <tr key={player.id}><td><button className="player-button" onClick={() => setPlayerId(player.id)}>{player.number && <span className="jersey">{player.number}</span>}<span>{player.name}<small>{player.position || 'Position unavailable'} · {player.active ? 'Current roster' : 'Former / historical'}</small></span></button></td>
              <td>{values.records || 'No records'}</td><td className="metric-cell">{display(values, metric)}</td><td>{display(values, 'distance_m')}</td><td>{display(values, 'minutes')}</td><td>{display(values, 'speed_max')}</td><td>{values.records ? `${values.observed[metric]} / ${values.records}${cache.legacy ? '*' : ''}` : '—'}</td></tr>; })}</tbody></table></div>
            : <div className="empty compact"><p>No player statistics in this selection.</p></div>}
        </section>
        <section className="panel"><div className="section-heading"><div><h3>Session log</h3><p>All {view.sessions.length} cached sessions in this selection, including sessions without statistics. Select a row for source labels and individual records.</p></div>
          <span className="coverage-badge">{view.sessions.length - view.withStats} without stats</span></div>
          {view.sessions.length > 0 && <div className="table-scroll"><table><thead><tr><th>Date / session</th><th>Classification</th><th>Players with stats</th><th>{chosen.label}<small>{chosen.unit}</small></th><th>Record check</th></tr></thead><tbody>
            {view.sessions.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(session => [
              <tr key={session.id}><td><button className="session-button" aria-expanded={expanded === session.id} onClick={() => setExpanded(expanded === session.id ? null : session.id)}>{dateLabel(session.date, true)}<small>#{session.id} · {expanded === session.id ? 'Hide' : 'View'} details</small></button></td>
                <td><TypePill category={session.category} /></td><td>{session.playerIds.length}{session.assignmentsKnown ? ` / ${session.assignedIds.filter(id => (roster !== 'active' || cache.players.get(id)?.active) && (playerId === null || playerId === id)).length} assigned` : ' · assignments unknown'}</td>
                <td className="metric-cell">{display(session.metrics, metric)}</td><td>{session.metrics.records === 0 ? 'Missing stats' : session.zeroRecords ? `${session.zeroRecords} all-zero records` : cache.legacy ? 'Legacy cache*' : 'Records present'}</td></tr>,
              expanded === session.id && <tr key={session.id + '-detail'}><td colSpan={5}>{sessionDetails(session)}</td></tr>,
            ])}
          </tbody></table></div>}
          {pageCount > 1 && <div className="pagination"><span>Page {currentPage + 1} of {pageCount}</span><button className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button><button className="button secondary" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>Next</button></div>}
        </section>
        <section className="method-notes"><h3>How to read this view</h3><p>Roster status reflects the last sync, not a live roster check. Separate Kinexon player IDs are not merged, even when names are similar. Exposure is tracked time, not official playing time. Movement actions = accelerations + decelerations + changes of direction; no intensity threshold is implied. Court transitions combine mid-court and full-court events. Peak speed is a maximum, all other metrics are sums.</p>
          <p>— means no measurement. A recorded zero remains zero. {view.zeroRecords > 0 && `${view.zeroRecords} all-zero player-session records need a data-quality check. `}{cache.legacy && '* In this legacy cache, stored zero and originally missing values cannot be distinguished; measured coverage is provisional.'}</p><p>Game/practice comparison uses the same roster and dates. Averages are per measured player-session record, not per game or per minute. Sensor-quality percentages appear only when supplied by Kinexon.</p></section>
      </>}
      <footer><span>GT · McCamish performance console</span><span>Read-only Kinexon data · Team 3 · America/New_York</span></footer>
    </div>
  </main>;
}
