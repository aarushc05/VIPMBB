import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, BarChart3, BookOpen, CalendarDays, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, ClipboardList, Database, Download, FileText, Filter, HardDrive, Info, LoaderCircle, LockKeyhole, Menu, MessageSquare, Plus, RefreshCw, Search, Send, Settings2, ShieldCheck, Sparkles, Trash2, TriangleAlert, Users, X } from 'lucide-react';
import { api, post } from './api.js';
import { renderApp } from './mount.js';
import { appRoute, dateLabel, dateRange, jobDateRange, number, percentage, sessionLabels, sortRows, timestampLabel, timeLabel, validRange } from './format.js';
import './styles.css';

const NAV = [
  { key: 'practices', label: 'Practice reports', icon: ClipboardList },
  { key: 'assistant', label: 'Ask the assistant', icon: MessageSquare },
  { key: 'knowledge', label: 'Knowledge library', icon: BookOpen },
  { key: 'system', label: 'System & data', icon: Settings2 },
];

function useResource(path, refresh = 0) {
  const [state, setState] = useState({ data: null, error: '', loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState(previous => ({ ...previous, error: '', loading: true }));
    api(path, { signal: controller.signal }).then(data => setState({ data, error: '', loading: false })).catch(error => {
      if (error.name !== 'AbortError') setState({ data: null, error: error.message, loading: false });
    });
    return () => controller.abort();
  }, [path, refresh]);
  return state;
}

function Spinner({ label = 'Loading…' }) { return <span className="spinner-label"><LoaderCircle size={17} className="spin" />{label}</span>; }
function Banner({ children, tone = 'info', title, className = '' }) {
  const Icon = tone === 'error' || tone === 'warning' ? TriangleAlert : tone === 'success' ? CheckCircle2 : Info;
  return <div className={`banner ${tone} ${className}`} role={tone === 'error' ? 'alert' : undefined}><Icon size={18} /><div>{title && <strong>{title}</strong>}{children}</div></div>;
}
function Empty({ icon: Icon = ClipboardList, title, children, action }) {
  return <div className="empty"><div className="empty-icon"><Icon size={29} strokeWidth={1.5} /></div><h3>{title}</h3><div>{children}</div>{action}</div>;
}
function Badge({ children, tone = 'neutral', dot = false }) { return <span className={`badge ${tone}`}>{dot && <span className="status-dot" />}{children}</span>; }
function Classification({ session }) {
  const kind = session?.classification || 'unknown';
  return <Badge tone={session?.reviewed ? kind === 'practice' ? 'green' : kind === 'game' ? 'blue' : 'neutral' : 'amber'}>{session?.reviewed ? 'Reviewed' : 'Unreviewed'} · {kind === 'unknown' ? 'Activity unknown' : kind}</Badge>;
}
function Stat({ label, value, suffix, detail, icon: Icon }) {
  return <div className="stat"><div className="stat-label">{label}{Icon && <Icon size={17} />}</div><div className="stat-value">{value}{suffix && <span>{suffix}</span>}</div>{detail && <div className="stat-detail">{detail}</div>}</div>;
}
function PageHeading({ eyebrow, title, description, children }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>{children && <div className="heading-actions">{children}</div>}</div>;
}
function Panel({ title, description, children, action, className = '' }) {
  return <section className={`panel ${className}`}><header className="panel-heading"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</header>{children}</section>;
}
function SafeValue({ value }) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return number(value, 2);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function parseRoute() {
  return appRoute(window.location.pathname, window.location.hash);
}

function App() {
  const [route, setRoute] = useState(parseRoute);
  const [refresh, setRefresh] = useState(0);
  const [mobileNav, setMobileNav] = useState(false);
  const [narrowScreen, setNarrowScreen] = useState(() => window.matchMedia('(max-width: 760px)').matches);
  const menuRef = useRef(null);
  const sidebarRef = useRef(null);
  const status = useResource('/status', refresh);
  const reload = useCallback(() => setRefresh(value => value + 1), []);
  const closeNavigation = useCallback(() => {
    setMobileNav(false);
    menuRef.current?.focus();
  }, []);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)');
    const update = event => setNarrowScreen(event.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!narrowScreen || !mobileNav) return;
    const focusable = () => [...(sidebarRef.current?.querySelectorAll('a[href],button:not(:disabled),[tabindex="0"]') || [])].filter(element => element.getClientRects().length > 0);
    const frame = requestAnimationFrame(() => focusable()[0]?.focus());
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const trapFocus = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeNavigation();
      } else if (event.key === 'Tab') {
        const elements = focusable();
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && (document.activeElement === first || !sidebarRef.current?.contains(document.activeElement))) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !sidebarRef.current?.contains(document.activeElement))) {
          event.preventDefault(); first?.focus();
        }
      }
    };
    document.addEventListener('keydown', trapFocus);
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', trapFocus);
    };
  }, [narrowScreen, mobileNav, closeNavigation]);
  useEffect(() => { const timer = window.setInterval(reload, 30000); return () => window.clearInterval(timer); }, [reload]);
  useEffect(() => {
    const handler = () => { setRoute(parseRoute()); setMobileNav(false); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  const navigate = (page, id) => { window.location.hash = id ? `${page}/${id}` : page; };
  const active = route.page === 'report' ? 'practices' : route.page;
  const data = status.data;
  return <div className="app-shell">
    <a href="#main-content" className="skip-link" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>Skip to content</a>
    <aside id="main-navigation" ref={sidebarRef} className={`sidebar ${mobileNav ? 'is-open' : ''}`} inert={narrowScreen && !mobileNav} aria-hidden={narrowScreen && !mobileNav ? true : undefined}>
      <div className="brand"><div className="brand-monogram">GT<span>▱</span></div><div><strong>Practice Intelligence</strong><span>MEN’S BASKETBALL</span></div><button className="icon-button close-nav" aria-label="Close navigation" onClick={closeNavigation}><X size={20} /></button></div>
      <div className="sidebar-section-label">YOUR WORKSPACE</div>
      <nav aria-label="Main navigation">{NAV.map(({ key, label, icon: Icon }) => <a key={key} href={`#${key}`} className={active === key ? 'active' : ''} aria-current={active === key ? 'page' : undefined}><Icon size={19} strokeWidth={1.7} /><span>{label}</span>{active === key && <span className="nav-indicator" />}</a>)}</nav>
      <div className="sidebar-note"><div className="court-mark"><div /><span /></div><h3>Better questions.<br />Clearer practice.</h3><p>Evidence for the conversations that move your team forward.</p></div>
      <div className="local-badge"><LockKeyhole size={15} /><div><strong>Private local workspace</strong><span>Stored on this laptop</span></div></div>
    </aside>
    {mobileNav && <button className="nav-backdrop" aria-label="Close navigation" onClick={closeNavigation} />}
    <div className="main-shell">
      <header className="topbar"><div className="topbar-left"><button ref={menuRef} className="icon-button mobile-menu" aria-label="Open navigation" aria-expanded={mobileNav} aria-controls="main-navigation" onClick={() => setMobileNav(true)}><Menu size={22} /></button><span className="topbar-org">GEORGIA TECH</span><span className="topbar-separator">/</span><span>Men’s Basketball</span></div><div className="topbar-right"><span className="local-indicator"><span />Local workspace</span><span className="team-avatar">GT</span></div></header>
      <main id="main-content" tabIndex={-1} className={`main-content ${route.page === 'assistant' ? 'chat-main' : ''}`}>
        {status.error && <Banner tone="error" title="Connection issue"><p>{status.error}</p><button className="text-button" onClick={reload}>Retry connection</button></Banner>}
        {route.page === 'practices' && <Practices status={data} navigate={navigate} />}
        {route.page === 'report' && <Report id={route.id} navigate={navigate} onChange={reload} />}
        {route.page === 'assistant' && <Assistant sessionId={route.id} status={data} navigate={navigate} />}
        {route.page === 'knowledge' && <Knowledge documentId={route.id} />}
        {route.page === 'system' && <System status={data} reload={reload} />}
      </main>
      <footer className="app-footer"><span><ShieldCheck size={14} /> Source-linked analysis. Coaching decisions stay with you.</span><a href="#system">{data?.data?.latest ? `Latest activity: ${dateLabel(data.data.latest)}` : 'View data status'}<ArrowUpRight size={13} /></a></footer>
    </div>
  </div>;
}

function Practices({ status, navigate }) {
  const [preset, setPreset] = useState('all');
  const [custom, setCustom] = useState({ start: '', end: '' });
  const [appliedCustom, setAppliedCustom] = useState({ start: '', end: '' });
  const [classification, setClassification] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setPage(0); }, 250); return () => clearTimeout(timer); }, [search]);
  const dates = preset === 'custom' ? appliedCustom : dateRange(preset, status?.today);
  const params = new URLSearchParams({ limit: '30', offset: String(page * 30) });
  if (dates.start) params.set('start', dates.start);
  if (dates.end) params.set('end', dates.end);
  if (classification) params.set('classification', classification);
  if (query) params.set('q', query);
  const result = useResource(`/sessions?${params}`, `${refresh}:${status?.data?.last_sync || ''}:${status?.data?.sessions || ''}`);
  const sessions = result.data?.sessions || [];
  const total = result.data?.total || 0;
  const changePreset = value => { setPreset(value); setPage(0); };
  return <>
    <PageHeading eyebrow="THE PRACTICE DESK" title="Every practice. In perspective." description="Explore player workloads, find useful patterns, and bring better questions to the floor."><button className="button primary" onClick={() => navigate('system')}><RefreshCw size={16} />Sync practice data</button></PageHeading>
    <div className="stats-grid three"><Stat label="Recorded sessions" value={number(status?.data?.sessions)} detail="All imported activity types" icon={ClipboardList} /><Stat label="Players in the archive" value={number(status?.data?.players)} detail="Historical and current participants" icon={Users} /><Stat label="Latest recorded activity" value={status?.data?.latest ? dateLabel(status.data.latest, { year: undefined }) : 'No data yet'} detail={status?.data?.last_sync ? `Last sync ${dateLabel(status.data.last_sync)}` : 'No successful live sync recorded'} icon={CalendarDays} /></div>
    <div className="library-context"><div><span className="gold-line" /><span><strong>Practice reports start with good context.</strong> Source labels are not verification. Review activity type before using sessions in practice comparisons.</span></div><a href="#knowledge">How we measure<ArrowUpRight size={15} /></a></div>
    <section className="panel library-panel">
      <div className="library-header"><div><h2>Session library <span className="count">{number(total)}</span></h2><p>Open any session to see its report and data-quality notes.</p></div><button className="button quiet small" aria-label="Refresh session library" disabled={result.loading} onClick={() => setRefresh(v => v + 1)}><RefreshCw size={15} />Refresh</button></div>
      <div className="filters"><div className="segmented" aria-label="Date range">{[['all', 'All history'], ['week', 'Last 7 days'], ['month', 'Last 30 days'], ['custom', 'Custom']].map(([value, label]) => <button key={value} aria-pressed={preset === value} className={preset === value ? 'selected' : ''} onClick={() => changePreset(value)} disabled={!status?.today && ['week', 'month'].includes(value)}>{label}</button>)}</div><div className="filter-right"><label className="search-field"><Search size={16} /><input aria-label="Search sessions" placeholder="Search sessions…" value={search} onChange={e => setSearch(e.target.value)} />{search && <button aria-label="Clear session search" onClick={() => setSearch('')}><X size={14} /></button>}</label><label className="select-field"><Filter size={15} /><select aria-label="Filter by activity type" value={classification} onChange={e => { setClassification(e.target.value); setPage(0); }}><option value="">All activity</option><option value="practice">Practice</option><option value="game">Game</option><option value="unknown">Unknown</option></select><ChevronDown size={13} /></label></div></div>
      {preset === 'custom' && <form className="date-form" onSubmit={e => { e.preventDefault(); if (validRange(custom.start, custom.end)) { setAppliedCustom({ ...custom }); setPage(0); } }}><label>From<input type="date" aria-label="Custom start date" value={custom.start} onChange={e => setCustom({ ...custom, start: e.target.value })} /></label><label>Through<input type="date" aria-label="Custom end date" value={custom.end} onChange={e => setCustom({ ...custom, end: e.target.value })} /></label><button className="button secondary small" disabled={!validRange(custom.start, custom.end)}>Apply dates</button>{!validRange(custom.start, custom.end) && <span className="field-error">Start date must be on or before end date.</span>}</form>}
      <div className="range-description"><CalendarDays size={14} />{dates.start || dates.end ? `${dates.start ? dateLabel(dates.start) : 'Beginning of archive'} – ${dates.end ? dateLabel(dates.end) : 'Present'}` : 'Entire available archive'}<span>America/New_York · date filters never shift to the last active week</span></div>
      {result.error && <div className="panel-padding"><Banner tone="error">{result.error}</Banner></div>}
      {result.loading ? <div className="loading-region"><Spinner label="Finding sessions…" /></div> : sessions.length ? <><div className="table-scroll"><table className="sessions-table"><thead><tr><th>Session</th><th>Activity review</th><th>Source label</th><th className="numeric">Players with data</th><th>Report</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{sessions.map(session => <tr key={session.id}><td><button className="session-link" onClick={() => navigate('report', session.id)}><span className="session-date-icon"><CalendarDays size={18} /></span><span><strong>{session.title || `Session ${session.id}`}</strong><span>{dateLabel(session.date || session.start)} · {timeLabel(session.start)} ET</span></span></button></td><td><Classification session={session} /></td><td><span className="source-label">{sessionLabels(session.source_labels)}</span></td><td className="numeric">{number(session.player_count)}</td><td><span className={`report-status ${session.status === 'complete' ? 'ready' : ''}`}><span />{session.status === 'complete' ? 'Available' : session.status || 'Review coverage'}</span>{session.report_version ? <span className="report-version">v{session.report_version}</span> : null}</td><td><button className="icon-button" aria-label={`Open report for ${session.title || `session ${session.id}`}`} onClick={() => navigate('report', session.id)}><ArrowRight size={18} /></button></td></tr>)}</tbody></table></div><div className="pagination"><span>Showing {page * 30 + 1}–{Math.min((page + 1) * 30, total)} of {number(total)} sessions</span><div><button className="button quiet small" disabled={page === 0} onClick={() => setPage(p => p - 1)}><ChevronLeft size={15} />Previous</button><button className="button quiet small" disabled={(page + 1) * 30 >= total} onClick={() => setPage(p => p + 1)}>Next<ChevronRight size={15} /></button></div></div></> : !result.error && <Empty title="No sessions in this view" icon={CalendarDays} action={<button className="button secondary" onClick={() => { setPreset('all'); setClassification(''); setSearch(''); setPage(0); }}>View all history<ArrowRight size={16} /></button>}><p>{dates.start || dates.end ? 'No recorded activity was found for these dates. Empty periods stay empty; we won’t substitute an older practice.' : 'Try removing filters or sync your Kinexon data from System & data.'}</p>{status?.data?.latest && <p className="subtle">Latest activity in the archive: {dateLabel(status.data.latest)}.</p>}</Empty>}
    </section>
  </>;
}

const METRICS = {
  workload: [ ['minutes', 'Exposure', 'min', 1], ['mechanical_load', 'Mechanical load', 'AU', 1], ['load_per_minute', 'Load / minute', 'AU/min', 2], ['distance_m', 'Distance', 'm', 0], ['accel_load', 'Acceleration load', 'Kinexon units', 1] ],
  movement: [ ['minutes', 'Exposure', 'min', 1], ['speed_max', 'Peak speed', 'm/s', 2], ['acceleration_count', 'Accelerations', 'count', 0], ['deceleration_count', 'Decelerations', 'count', 0], ['change_of_direction_count', 'Direction changes', 'count', 0], ['jump_count', 'Jumps', 'count', 0] ],
  metabolic: [ ['minutes', 'Exposure', 'min', 1], ['metabolic_work', 'Metabolic work', 'source units', 1], ['distance_m', 'Distance', 'm', 0], ['load_per_minute', 'Load / minute', 'AU/min', 2] ],
};

function Report({ id, navigate, onChange }) {
  const [refresh, setRefresh] = useState(0);
  const { data: report, loading, error } = useResource(`/reports/${id}`, refresh);
  const [group, setGroup] = useState('workload');
  const [sort, setSort] = useState({ key: 'mechanical_load', direction: 'desc' });
  const [playerSearch, setPlayerSearch] = useState('');
  const [action, setAction] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [reviewOpen, setReviewOpen] = useState(false);
  const [classification, setClassification] = useState('practice');
  const [reason, setReason] = useState('');
  useEffect(() => { setGroup('workload'); setPlayerSearch(''); setReviewOpen(false); setNotice(''); }, [id]);
  const perform = async (kind, operation) => {
    setAction(kind); setActionError(''); setNotice('');
    try { await operation(); setRefresh(v => v + 1); onChange(); setNotice(kind === 'review' ? 'Activity review saved. Coverage has not been changed.' : 'Report checked against the latest source data. An unchanged report keeps its version.'); setReviewOpen(false); }
    catch (e) { setActionError(e.message); } finally { setAction(''); }
  };
  if (loading && !report) return <div className="loading-region tall"><Spinner label="Preparing the report…" /></div>;
  if (error || !report) return <><button className="back-link" onClick={() => navigate('practices')}><ArrowLeft size={15} />Session library</button><Banner tone="error">{error || 'This report is unavailable.'}</Banner></>;
  const session = report.session || {};
  const coverage = report.coverage || {};
  const summary = report.summary || {};
  const metrics = METRICS[group];
  const players = sortRows((report.players || []).filter(p => `${p.name} ${p.number ?? ''}`.toLowerCase().includes(playerSearch.toLowerCase())), sort.key, sort.direction, (p, key) => key === 'name' ? p.name : p.metrics?.[key]);
  const maxLoad = Math.max(0, ...(report.players || []).map(p => Number(p.metrics?.mechanical_load || 0)));
  const toggleSort = key => setSort(previous => ({ key, direction: previous.key === key && previous.direction === 'desc' ? 'asc' : 'desc' }));
  return <>
    <button className="back-link" onClick={() => navigate('practices')}><ArrowLeft size={15} />Session library</button>
    <PageHeading eyebrow={`SESSION ${id} · REPORT V${report.version || session.report_version || 1}`} title={session.title || 'Practice report'} description={`${dateLabel(session.date || session.start)} · ${timeLabel(session.start)} – ${timeLabel(session.end)} ET`}><button className="button secondary" onClick={() => navigate('assistant', id)}><MessageSquare size={16} />Ask about this session</button><a className="button primary" href={`/api/reports/${id}/print`} target="_blank" rel="noopener noreferrer"><Download size={16} />Print / save PDF</a></PageHeading>
    <div className="report-metadata"><Classification session={session} /><Badge tone={coverage.complete ? 'green' : 'amber'}>{coverage.complete ? 'Core player coverage checked' : 'Coverage needs attention'}</Badge><span>Source: {sessionLabels(session.source_labels)}</span><button className="text-button" onClick={() => { setReviewOpen(!reviewOpen); setClassification(session.classification || 'unknown'); }}>Review activity type<ChevronDown size={13} /></button></div>
    {actionError && <Banner tone="error">{actionError}</Banner>}{notice && <Banner tone="success">{notice}</Banner>}
    {reviewOpen && <form className="review-form panel" onSubmit={e => { e.preventDefault(); perform('review', () => post(`/sessions/${id}/review`, { classification, reason })); }}><div><h3>Confirm the activity—not its completeness</h3><p>Only mark an activity when you know what this recording contains. Mixed or uncertain sessions should remain unknown.</p></div><label>Activity type<select value={classification} onChange={e => setClassification(e.target.value)}><option value="practice">Practice</option><option value="game">Game</option><option value="unknown">Unknown / mixed</option></select></label><label className="review-reason">Review note<input required minLength={3} maxLength={1000} placeholder="How did you verify this activity?" value={reason} onChange={e => setReason(e.target.value)} /></label><div className="review-actions"><button type="button" className="button quiet" onClick={() => setReviewOpen(false)}>Cancel</button><button className="button primary" disabled={!!action || reason.trim().length < 3}>{action === 'review' ? <Spinner label="Saving…" /> : <><Check size={16} />Save review</>}</button></div></form>}
    {(report.warnings?.length > 0 || coverage.notes?.length > 0) && <Banner tone="warning" title="Read this report with its coverage in mind"><ul>{[...new Set([...(report.warnings || []), ...(coverage.notes || [])])].map((warning, i) => <li key={i}>{typeof warning === 'string' ? warning : JSON.stringify(warning)}</li>)}</ul></Banner>}
    <div className="stats-grid four"><Stat label="Players with measurements" value={number(coverage.recorded_players ?? summary.players)} detail={coverage.expected_players === null || coverage.expected_players === undefined ? 'Expected participant count not verified' : `Of ${number(coverage.expected_players)} expected participants`} icon={Users} /><Stat label="Total player exposure" value={number(summary.minutes_total, 1)} suffix="min" detail="Sum across players, not practice length" icon={CalendarDays} /><Stat label="Recorded distance" value={number(summary.distance_m == null ? null : summary.distance_m / 1000, 2)} suffix="km" detail="Total across available player records" icon={Activity} /><Stat label="Mechanical load" value={number(summary.mechanical_load, 0)} suffix="AU" detail="Descriptive workload, not an effort score" icon={BarChart3} /></div>
    <div className="report-two-column"><Panel title={session.classification === 'practice' ? 'Practice observations' : 'Recording observations'} description="Calculated from this report. No inferred fatigue or effort."><div className="observations">{report.observations?.length ? report.observations.map((observation, index) => <div className="observation" key={index}><span className="observation-number">{String(index + 1).padStart(2, '0')}</span><div><h3>{observation.title}</h3><p>{observation.body}</p></div></div>) : <p className="muted panel-padding">Not enough verified data for comparative observations. The player measurements are shown below.</p>}</div></Panel><Panel title="Report provenance" description="Know what sits behind the numbers."><dl className="detail-list"><div><dt>Generated</dt><dd>{timestampLabel(report.generated_at)}</dd></div><div><dt>Source updated</dt><dd>{timestampLabel(session.updated_at)}</dd></div><div><dt>Activity type</dt><dd>{session.reviewed ? 'Reviewed by a user' : 'Not independently reviewed'}</dd></div><div><dt>Measurement coverage</dt><dd>{coverage.complete ? 'Core metrics for expected players' : 'Incomplete or unverified'}</dd></div><div><dt>Comparison baseline</dt><dd>Earlier reviewed practices only</dd></div></dl><div className="panel-action"><button className="button secondary small" disabled={!!action} onClick={() => perform('regenerate', () => post(`/reports/${id}/regenerate`))}>{action === 'regenerate' ? <Spinner label="Checking…" /> : <><RefreshCw size={14} />Check for report updates</>}</button></div></Panel></div>
    <Panel title="Player workload" description="All recorded players. A dash means unavailable, never zero." action={<label className="search-field"><Search size={15} /><input value={playerSearch} aria-label="Find a player in this report" placeholder="Find a player…" onChange={e => setPlayerSearch(e.target.value)} /></label>}>
      <div className="metric-tabs" role="group" aria-label="Metric group">{[['workload', 'Workload'], ['movement', 'Movement'], ['metabolic', 'Metabolic']].map(([key, label]) => <button key={key} className={group === key ? 'selected' : ''} aria-pressed={group === key} onClick={() => setGroup(key)}>{label}</button>)}</div>
      {group === 'metabolic' && <div className="inline-note"><Info size={15} />Metabolic work is shown in source units until the vendor’s unit definition is verified. It is not a basketball efficiency score.</div>}
      {players.length ? <div className="table-scroll"><table className="player-table"><thead><tr><th aria-sort={sort.key === 'name' ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button onClick={() => toggleSort('name')}>Player<SortMark field="name" sort={sort} /></button></th>{metrics.map(([key, label, unit]) => <th className="numeric" key={key} aria-sort={sort.key === key ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button onClick={() => toggleSort(key)}>{label}<SortMark field={key} sort={sort} /><small>{unit}</small></button></th>)}</tr></thead><tbody>{players.map(player => <tr key={player.id}><td><div className="player-label"><span className="player-number">{player.number ?? '—'}</span><div><strong>{player.name || `Player ${player.id}`}</strong>{player.missing_metrics?.length > 0 && <span className="missing-note">{player.missing_metrics.length} missing measurements</span>}</div></div></td>{metrics.map(([key, , , digits]) => <td className="numeric" key={key}>{key === 'mechanical_load' ? <div className="load-cell"><span>{number(player.metrics?.[key], digits)}</span><div className="load-track"><div style={{ width: `${maxLoad > 0 ? Math.min(100, Math.max(0, Number(player.metrics?.[key] || 0) / maxLoad * 100)) : 0}%` }} /></div></div> : number(player.metrics?.[key], digits)}</td>)}</tr>)}</tbody></table></div> : <Empty title={playerSearch ? 'No matching players' : 'No player measurements'} icon={Users}><p>{playerSearch ? 'Try another name or jersey number.' : 'This session does not have usable player statistics yet. Check coverage or sync again.'}</p></Empty>}
      <div className="table-footnote">Exposure is recorded player time, not official playing time. AU = arbitrary units. Comparisons require consistent metric definitions.</div>
    </Panel>
    <div className="report-two-column"><Panel title="Individual context" description={session.classification === 'practice' ? 'Load per minute against each player’s earlier reviewed practices.' : 'Practice-to-practice comparison is unavailable for this activity type.'}>{report.players?.some(player => player.baseline?.sample_count > 0) ? <div className="table-scroll"><table><thead><tr><th>Player</th><th className="numeric">Prior practices</th><th className="numeric">Baseline AU/min</th><th className="numeric">Change</th></tr></thead><tbody>{report.players.map(player => <tr key={player.id}><td>{player.name}</td><td className="numeric">{number(player.baseline?.sample_count)}</td><td className="numeric">{number(player.baseline?.load_per_minute, 2)}</td><td className="numeric">{percentage(player.baseline?.change_pct)}</td></tr>)}</tbody></table></div> : <Empty title={session.classification === 'practice' ? 'No eligible baseline yet' : 'Practice comparison unavailable'} icon={BarChart3}><p>{session.classification === 'practice' ? 'Comparisons need earlier reviewed practices with usable exposure and workload. Current and future sessions are excluded.' : 'Only recordings classified as practice are eligible for practice baselines. Games, mixed activity and unknown recordings are not compared with practice workloads.'}</p></Empty>}<div className="table-footnote">A change is descriptive—not a fatigue, readiness or effort diagnosis.</div></Panel><Panel title="Drill breakdown" description="Recorded phases and available player measurements.">{report.drills?.length ? <DrillBreakdown drills={report.drills} players={report.players || []} /> : <Empty title="No drill-level data imported" icon={Activity}><p>This does not mean the practice had no drills. Phase metadata and player-phase measurements must be available before drill analysis can be shown.</p></Empty>}</Panel></div>
    {report.definitions?.length > 0 && <details className="panel definitions-panel"><summary><BookOpen size={17} />Metric definitions and calculation notes<ChevronDown size={16} /></summary><div className="definition-grid">{report.definitions.map((definition, index) => typeof definition === 'string' ? <p key={index}>{definition}</p> : <div key={index}><h3>{definition.title || definition.name || definition.key || definition.metric}</h3><p>{definition.body || definition.description || definition.definition}</p>{definition.unit && <small>Unit: {definition.unit}</small>}</div>)}</div></details>}
    {report.reviews?.length > 0 && <details className="panel definitions-panel"><summary><ShieldCheck size={17} />Activity review history<ChevronDown size={16} /></summary><div className="review-history">{report.reviews.map((review, index) => <div key={index}><Badge>{review.classification}</Badge><span>{timestampLabel(review.created_at)}</span><p>{review.reason}</p></div>)}</div></details>}
  </>;
}
function SortMark({ sort, field }) { return sort.key === field ? sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : null; }

function DrillBreakdown({ drills, players }) {
  const names = new Map(players.map(player => [player.id, player.name]));
  return <div className="drill-list">{drills.map(drill => <details className="drill-detail" key={drill.id}>
    <summary className="drill-item"><span className="drill-icon"><Activity size={17} /></span><div><strong>{drill.title || `Phase ${drill.id}`}</strong><span>{timeLabel(drill.start)} – {timeLabel(drill.end)} ET</span></div><Badge tone={drill.valid === false ? 'amber' : 'neutral'}>{drill.valid === false ? 'Review bounds' : `${number(drill.player_count)} players`}</Badge><ChevronDown size={13} /></summary>
    {drill.valid === false && <div className="inline-note"><TriangleAlert size={14} />This phase has invalid or overlapping boundaries. Its values must not be added to other phase totals.</div>}
    {drill.players?.length ? <div className="table-scroll"><table><thead><tr><th>Player</th><th className="numeric">Exposure<small>min</small></th><th className="numeric">Load<small>AU</small></th><th className="numeric">Load/min<small>AU/min</small></th><th className="numeric">Distance<small>m</small></th></tr></thead><tbody>{drill.players.map(player => <tr key={player.id}><td>{player.name || names.get(player.id) || `Player ${player.id}`}</td><td className="numeric">{number(player.metrics?.minutes, 1)}</td><td className="numeric">{number(player.metrics?.mechanical_load, 1)}</td><td className="numeric">{number(player.metrics?.load_per_minute, 2)}</td><td className="numeric">{number(player.metrics?.distance_m)}</td></tr>)}</tbody></table></div> : <p className="drill-unavailable">Player-phase statistics are not available for this phase.</p>}
  </details>)}</div>;
}

function Assistant({ sessionId, status, navigate }) {
  const storageKey = sessionId ? `vipmbb-conversation-${sessionId}` : 'vipmbb-conversation';
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [error, setError] = useState('');
  const [clearConfirm, setClearConfirm] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const contextVersion = useRef(0);
  useEffect(() => {
    const version = ++contextVersion.current;
    let saved;
    try { saved = localStorage.getItem(storageKey); } catch { /* Private mode may disable storage. */ }
    setConversationId(saved || null); setMessages([]); setError(''); setMessage(''); setBusy(false); setHistoryLoading(!!saved); setClearConfirm(false);
    if (saved) api(`/chat/history?conversation_id=${encodeURIComponent(saved)}`).then(result => { if (version === contextVersion.current) setMessages(normalizeHistory(result.messages || [])); }).catch(e => { if (version === contextVersion.current) setError(e.message); }).finally(() => { if (version === contextVersion.current) setHistoryLoading(false); });
  }, [storageKey]);
  useEffect(() => { if (messages.length || busy) bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, busy]);
  const send = async (text = message) => {
    const trimmed = text.trim();
    if (!trimmed || busy || historyLoading) return;
    const version = contextVersion.current;
    setBusy(true); setError(''); setMessage('');
    setMessages(previous => [...previous, { role: 'user', content: trimmed }]);
    try {
      const result = await post('/chat', { message: trimmed, ...(conversationId ? { conversation_id: conversationId } : {}), ...(sessionId ? { session_id: sessionId } : {}) });
      if (version !== contextVersion.current) return;
      setConversationId(result.conversation_id);
      try { localStorage.setItem(storageKey, result.conversation_id); } catch { /* Conversation still works without local storage. */ }
      setMessages(previous => [...previous, { role: 'assistant', ...result }]);
    } catch (e) {
      if (version === contextVersion.current) { setError(e.message); setMessages(previous => previous.slice(0, -1)); setMessage(trimmed); }
    } finally { if (version === contextVersion.current) { setBusy(false); setTimeout(() => inputRef.current?.focus(), 0); } }
  };
  const clear = async () => {
    setError(''); setBusy(true);
    try { if (conversationId) await post('/chat/clear', { conversation_id: conversationId }); setMessages([]); setConversationId(null); try { localStorage.removeItem(storageKey); } catch {} setClearConfirm(false); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const examples = sessionId ? [ 'Summarize this session.', 'Who had the highest workload per minute in this session?', 'What data is missing from this session?', 'What does mechanical load mean?' ] : [ 'Summarize the latest practice.', 'Which players had the highest workload in the last 7 days?', 'Compare player workloads across the last 30 days.', 'What does mechanical load mean?' ];
  return <>
    <PageHeading eyebrow="YOUR ANALYSIS PARTNER" title="Ask a better question." description="Explore practice data in plain language. Every numerical answer starts with a calculation."><button className="button secondary small" disabled={busy || historyLoading || !messages.length} onClick={() => setClearConfirm(true)}><Trash2 size={15} />Clear conversation</button></PageHeading>
    <div className="assistant-status"><div><span className={`status-dot ${status?.model?.available ? 'green' : 'amber'}`} /><strong>{status?.model?.available ? 'Local model connected' : 'Bounded fallback mode'}</strong><span>{status?.model?.available ? status.model.model || 'On-device inference' : 'Model unavailable · supported questions still work'}</span></div><a href="#system">Model status<ArrowUpRight size={13} /></a></div>
    {sessionId && <div className="chat-context"><FileText size={16} /><span>Focused on <strong>session {sessionId}</strong></span><button className="text-button" onClick={() => navigate('report', sessionId)}>Open report<ArrowUpRight size={13} /></button><button className="icon-button" aria-label="Remove session context and open general conversation" disabled={busy} onClick={() => navigate('assistant')}><X size={16} /></button></div>}
    {clearConfirm && <Banner tone="warning" title="Clear this conversation?"><p>This deletes its saved messages on this laptop. Reports and source measurements are not affected.</p><div className="inline-actions"><button className="button danger small" onClick={clear} disabled={busy}>Yes, clear conversation</button><button className="button quiet small" onClick={() => setClearConfirm(false)}>Cancel</button></div></Banner>}
    <div className="chat-panel">
      {historyLoading ? <div className="loading-region"><Spinner label="Loading conversation…" /></div> : messages.length === 0 ? <div className="chat-welcome"><div className="assistant-emblem"><Sparkles size={30} strokeWidth={1.3} /></div><div className="eyebrow">CURIOUS IS A GOOD PLACE TO START</div><h2>From practice data<br />to a clearer picture.</h2><p>Ask about workloads, player exposure, recent trends, or the meaning behind a metric.</p><div className="prompt-grid">{examples.map((example, index) => <button key={example} onClick={() => send(example)} disabled={busy}><span className="prompt-icon">{index === 0 ? <ClipboardList size={17} /> : index === 1 ? <Users size={17} /> : index === 2 ? <BarChart3 size={17} /> : <BookOpen size={17} />}</span><span>{example}</span><ArrowUpRight size={15} /></button>)}</div><div className="chat-principle"><ShieldCheck size={15} />Numbers are calculated. Sources are linked. Uncertainty stays visible.</div></div> : <div className="message-list" aria-live="polite" aria-relevant="additions">{messages.map((entry, index) => entry.role === 'user' ? <div className="message user-message" key={index}><span className="message-avatar">YOU</span><div><span className="message-author">You</span><div className="message-body">{entry.content || entry.message}</div></div></div> : <div className="message assistant-message" key={index}><span className="message-avatar"><Sparkles size={17} /></span><div className="assistant-content"><div className="message-author">Practice assistant<Badge tone={entry.mode === 'local-model' ? 'green' : 'neutral'}>{entry.mode === 'local-model' ? 'Local model + source-backed tools' : 'Bounded fallback'}</Badge></div><div className="message-body">{entry.answer || entry.content}</div>{entry.query && <div className="query-context">{entry.query.start || entry.query.end ? <span><CalendarDays size={12} />{entry.query.start ? dateLabel(entry.query.start) : 'Archive start'} – {entry.query.end ? dateLabel(entry.query.end) : 'Present'}</span> : null}{entry.query.metric && !['definition', 'notes'].includes(entry.query.intent) && <span>Metric: {entry.query.metric.replaceAll('_', ' ')}</span>}</div>}{entry.table?.columns?.length > 0 && <div className="chat-result-table table-scroll"><table><thead><tr>{entry.table.columns.map((column, i) => <th key={column.key || i}>{column.label || column.key}{column.unit && <small>{column.unit}</small>}</th>)}</tr></thead><tbody>{entry.table.rows?.map((row, rowIndex) => <tr key={rowIndex}>{entry.table.columns.map((column, columnIndex) => <td key={column.key || columnIndex}><SafeValue value={Array.isArray(row) ? row[columnIndex] : row[column.key]} /></td>)}</tr>)}</tbody></table>{!entry.table.rows?.length && <div className="table-footnote">No matching measurements.</div>}</div>}{entry.warnings?.length > 0 && <div className="answer-warnings"><Info size={15} /><ul>{entry.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></div>}{entry.sources?.length > 0 && <div className="answer-sources"><div>Sources</div>{entry.sources.map((source, i) => <button key={`${source.type}-${source.id}-${i}`} onClick={() => navigate(source.type === 'session' ? 'report' : 'knowledge', source.id)}><FileText size={13} />{source.title || `${source.type} ${source.id}`}<ArrowUpRight size={12} /></button>)}</div>}{entry.suggestions?.length > 0 && <div className="followup-questions">{entry.suggestions.map((suggestion, i) => <button key={i} disabled={busy} onClick={() => send(typeof suggestion === 'string' ? suggestion : suggestion.text || suggestion.question)}>{typeof suggestion === 'string' ? suggestion : suggestion.text || suggestion.question}<ArrowRight size={13} /></button>)}</div>}</div></div>)}</div>}
      {busy && <div className="chat-thinking"><Sparkles size={17} /><Spinner label="Checking the data and supporting sources…" /></div>}
      {error && <div className="chat-error"><Banner tone="error">{error}</Banner></div>}
      <div ref={bottomRef} />
      <form className="composer" onSubmit={e => { e.preventDefault(); send(); }}><label className="sr-only" htmlFor="chat-message">Your question</label><textarea id="chat-message" ref={inputRef} value={message} placeholder={sessionId ? 'Ask about this session…' : 'Ask about practices, players, or metrics…'} rows={2} maxLength={2000} disabled={historyLoading} onChange={e => setMessage(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} /><div className="composer-bottom"><span><LockKeyhole size={12} />Local analysis · Enter to send</span><button className="send-button" disabled={!message.trim() || busy || historyLoading} aria-label="Send question">{busy ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={19} />}</button></div></form>
      <p className="chat-disclaimer">Physical output is not the same as effort, fatigue or basketball execution. Verify important conclusions with the source report.</p>
    </div>
  </>;
}

function normalizeHistory(messages) {
  return messages.map(entry => {
    if (entry.role === 'assistant') {
      let content = entry.response || entry.payload || entry.content;
      if (typeof content === 'string') { try { const parsed = JSON.parse(content); if (typeof parsed === 'object' && parsed) content = parsed; } catch {} }
      return typeof content === 'object' && content ? { ...entry, ...content, role: 'assistant' } : { ...entry, answer: entry.answer || String(content || '') };
    }
    return entry;
  });
}

function Knowledge({ documentId }) {
  const [refresh, setRefresh] = useState(0);
  const { data, loading, error } = useResource('/knowledge', refresh);
  const [search, setSearch] = useState('');
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [saved, setSaved] = useState(false);
  const docs = (data?.documents || []).filter(doc => `${doc.title} ${doc.body}`.toLowerCase().includes(search.toLowerCase()));
  useEffect(() => {
    if (documentId && data) {
      setSearch('');
      const timer = setTimeout(() => document.getElementById(`document-${documentId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 30);
      return () => clearTimeout(timer);
    }
  }, [documentId, data]);
  const save = async e => {
    e.preventDefault(); setBusy(true); setActionError(''); setSaved(false);
    try { await post('/knowledge', { title: title.trim(), body: body.trim() }); setTitle(''); setBody(''); setAdding(false); setRefresh(v => v + 1); setSaved(true); }
    catch (e) { setActionError(e.message); } finally { setBusy(false); }
  };
  return <>
    <PageHeading eyebrow="SHARED DEFINITIONS. BETTER ANSWERS." title="The knowledge behind the numbers." description="Metric definitions, drill context, and coaching notes the assistant can reference."><button className="button primary" onClick={() => { setAdding(!adding); setSaved(false); }}><Plus size={16} />Add coaching note</button></PageHeading>
    <Banner title="Context is evidence, not a command"><p>Notes stay local and can support assistant answers. Keep observations factual, include dates when relevant, and avoid unnecessary personal or medical information.</p></Banner>
    {saved && <Banner tone="success">Coaching note saved to the local knowledge library and available for text retrieval. A semantic-index refresh is queued automatically when the local embedding model is installed.</Banner>}
    {adding && <form className="panel note-form" onSubmit={save}><h2>Add a coaching note</h2><label>Title<input autoFocus required maxLength={160} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Transition drill format — October 7" /></label><label>Context or observation<textarea required rows={6} maxLength={12000} value={body} onChange={e => setBody(e.target.value)} placeholder="Describe the drill, change, or observation. Include relevant practice dates and distinguish observations from interpretations." /></label>{actionError && <Banner tone="error">{actionError}</Banner>}<div className="form-actions"><span><LockKeyhole size={13} />Saved on this laptop</span><button type="button" className="button quiet" onClick={() => setAdding(false)} disabled={busy}>Cancel</button><button className="button primary" disabled={busy || !title.trim() || !body.trim()}>{busy ? <Spinner label="Saving…" /> : <><Check size={15} />Save note</>}</button></div></form>}
    <div className="knowledge-toolbar"><h2>Library <span className="count">{number(data?.documents?.length || 0)}</span></h2><label className="search-field"><Search size={16} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search the knowledge library…" aria-label="Search knowledge library" /></label></div>
    {error && <Banner tone="error">{error}</Banner>}{loading ? <div className="loading-region"><Spinner label="Loading local knowledge…" /></div> : docs.length ? <div className="knowledge-grid">{docs.map(doc => <article className={`panel knowledge-card ${documentId === doc.id ? 'highlighted' : ''}`} id={`document-${doc.id}`} key={doc.id}><header><span className={`knowledge-icon ${doc.kind === 'note' || doc.kind === 'coach_note' ? 'note' : ''}`}>{doc.kind === 'note' || doc.kind === 'coach_note' ? <FileText size={19} /> : <BookOpen size={19} />}</span><Badge>{String(doc.kind || 'reference').replaceAll('_', ' ')}</Badge></header><h3>{doc.title}</h3><div className="knowledge-body">{doc.body}</div><footer>Updated {dateLabel(doc.updated_at)}</footer></article>)}</div> : <div className="panel"><Empty icon={BookOpen} title={search ? 'No matching references' : 'Your library is ready for context'}><p>{search ? 'Try a different phrase.' : 'Add your first coaching note to give the assistant useful practice context.'}</p></Empty></div>}
  </>;
}

function System({ status, reload }) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [jobsRefresh, setJobsRefresh] = useState(0);
  const [showOlderJobs, setShowOlderJobs] = useState(false);
  const jobsResult = useResource('/jobs', jobsRefresh);
  useEffect(() => { const timer = setInterval(() => setJobsRefresh(v => v + 1), 6000); return () => clearInterval(timer); }, []);
  const runAction = async (name, path, body = {}) => {
    setBusy(name); setError(''); setNotice('');
    try {
      const result = await post(path, body);
      setNotice(name === 'backup' ? `Consistent database backup created: ${result.filename}. ${result.created_at ? timestampLabel(result.created_at) : ''}` : `${name === 'sync' ? 'Data sync' : 'Knowledge indexing'} ${result.job?.status === 'completed' ? 'completed' : 'queued'}. ${result.job?.message || 'Follow its progress in the job history below.'}`);
      setJobsRefresh(v => v + 1); reload();
    } catch (e) { setError(e.message); } finally { setBusy(''); }
  };
  const data = status?.data || {};
  const model = status?.model || {};
  const jobs = jobsResult.data?.jobs || status?.jobs || [];
  const visibleJobs = showOlderJobs ? jobs : jobs.slice(0, 10);
  return <>
    <PageHeading eyebrow="LOCAL OPERATIONS" title="A clear view of your system." description="Check data coverage, manage imports, and keep the workspace healthy."><button className="button secondary" onClick={() => { reload(); setJobsRefresh(v => v + 1); }}><RefreshCw size={15} />Refresh status</button></PageHeading>
    {error && <Banner tone="error">{error}</Banner>}{notice && <Banner tone="success">{notice}</Banner>}
    {status?.warnings?.length > 0 && <Banner tone="warning" title="System notices"><ul>{status.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></Banner>}
    <div className="system-status-grid"><div className="panel system-status-card"><span className="system-icon"><Database size={21} /></span><h3>Data archive</h3><strong>{number(data.sessions)} sessions</strong><p>{data.earliest ? `${dateLabel(data.earliest)} – ${dateLabel(data.latest)}` : 'No imported sessions yet'}</p><Badge tone={data.sessions ? 'blue' : 'neutral'}>{data.source || 'Local SQLite database'}</Badge></div><div className="panel system-status-card"><span className="system-icon"><RefreshCw size={21} /></span><h3>Background worker</h3><strong>{status?.worker?.running ? 'Running' : 'Not running'}</strong><p>{status?.worker?.running ? 'Can process queued work while the laptop is awake.' : 'Start the app worker to process queued jobs.'}</p><Badge tone={status?.worker?.running ? 'green' : 'amber'} dot>{status?.worker?.running ? 'Available' : 'Needs attention'}</Badge></div><div className="panel system-status-card"><span className="system-icon"><Sparkles size={21} /></span><h3>Local assistant</h3><strong>{model.available ? 'Model connected' : 'Fallback available'}</strong><p>{model.model || 'qwen3:4b'} · local inference</p><Badge tone={model.available ? 'green' : 'amber'} dot>{model.available ? 'Local model ready' : 'Bounded questions only'}</Badge></div></div>
    <div className="record-coverage"><div><span className="record-count">{number(data.fresh_records)}</span><strong>Fetched player-session records</strong><span>Retrieved directly from Kinexon; completeness is checked per report.</span></div><div><span className="record-count">{number(data.legacy_records)}</span><strong>Legacy cache records</strong><span>Historical imports with unverified participant coverage and exposure.</span></div></div>
    <div className="system-columns"><Panel title="Import Kinexon data" description="Download new or revised sessions. Existing records are updated safely."><form className="sync-form" onSubmit={e => { e.preventDefault(); if (validRange(start, end)) runAction('sync', '/sync', { ...(start ? { start } : {}), ...(end ? { end } : {}) }); }}><div className="connection-state"><LockKeyhole size={17} /><div><strong>{status?.credentials_configured ? 'Credentials configured on this laptop' : 'Kinexon credentials are not configured'}</strong><p>{status?.credentials_configured ? 'Secret values are never sent to the browser or assistant.' : 'Add the required credentials to the protected local environment file. Do not enter them into chat.'}</p></div></div><div className="date-inputs"><label>Start date <span>optional</span><input type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label>End date <span>optional</span><input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></div>{!validRange(start, end) && <p className="field-error">Start date must be on or before end date.</p>}<p className="field-hint">Leave dates blank to use the worker’s default sync range. Dates use America/New_York.</p><button className="button primary" disabled={!!busy || !validRange(start, end) || !status?.credentials_configured}>{busy === 'sync' ? <Spinner label="Queuing sync…" /> : <><RefreshCw size={16} />Sync data</>}</button><div className="last-sync">Last successful live sync: <strong>{timestampLabel(data.last_sync)}</strong></div></form></Panel><Panel title="Local models & retrieval" description="No cloud model fallback. Calculations stay in the application."><div className="model-details"><dl className="detail-list"><div><dt>Language model</dt><dd>{model.model || 'qwen3:4b'}</dd></div><div><dt>Embedding model</dt><dd>{model.embedding_model || 'embeddinggemma'}</dd></div><div><dt>Installed models</dt><dd>{model.installed_models?.length ? model.installed_models.map(item => typeof item === 'string' ? item : item.name || item.model).join(', ') : 'None detected'}</dd></div><div><dt>Model service</dt><dd>127.0.0.1:11434</dd></div></dl>{model.error && <Banner tone="warning">{model.error}</Banner>}<p className="field-hint">Keyword retrieval works without embeddings. A semantic index rebuild uses your installed local embedding model.</p><button className="button secondary" disabled={!!busy} onClick={() => runAction('index', '/model/index')}>{busy === 'index' ? <Spinner label="Queuing…" /> : <><BookOpen size={16} />Rebuild knowledge index</>}</button></div></Panel></div>
    <Panel title="Background job history" description="Showing recent jobs with active work first. A queued job is not a completed import." action={<Badge>{number(jobs.length)} jobs</Badge>}>
      {jobsResult.error && <div className="panel-padding"><Banner tone="error">{jobsResult.error}</Banner></div>}
      {jobs.length ? <div className="table-scroll"><table><thead><tr><th>Job</th><th>Status</th><th>Date range</th><th>Details</th><th>Updated</th></tr></thead><tbody>{visibleJobs.map(job => <tr key={job.id}><td><strong>{String(job.kind || 'task').replaceAll('_', ' ')}</strong><span className="job-id">#{job.id}</span></td><td><Badge tone={job.status === 'completed' || job.status === 'succeeded' ? 'green' : job.status === 'failed' ? 'red' : job.status === 'running' ? 'blue' : 'amber'}>{job.status}</Badge></td><td className="job-range">{jobDateRange(job) || (job.payload?.session_id ? `Session ${job.payload.session_id}` : '—')}</td><td className="job-message">{job.message || job.error || 'Awaiting processing'}</td><td className="timestamp">{timestampLabel(job.updated_at || job.finished_at || job.created_at)}</td></tr>)}</tbody></table></div> : <Empty icon={RefreshCw} title="No background jobs yet"><p>Syncs and knowledge-index rebuilds will appear here.</p></Empty>}
      {jobs.length > 10 && <div className="pagination"><span>Showing {visibleJobs.length} of {jobs.length} loaded jobs · active work first</span><button className="button secondary small" aria-expanded={showOlderJobs} onClick={() => setShowOlderJobs(value => !value)}>{showOlderJobs ? 'Show latest 10' : `Show older jobs (${jobs.length - 10})`}<ChevronDown size={14} /></button></div>}
    </Panel>
    <div className="backup-panel panel"><span className="backup-icon"><HardDrive size={26} strokeWidth={1.5} /></span><div><h2>Keep a recoverable copy.</h2><p>Create a consistent local database backup, even while the app is running. Store another copy somewhere approved and secure.</p></div><button className="button secondary" disabled={!!busy} onClick={() => runAction('backup', '/backup')}>{busy === 'backup' ? <Spinner label="Backing up…" /> : <><Download size={16} />Create database backup</>}</button></div>
    <div className="offline-note"><Info size={16} /><p>This laptop must be awake and connected to download new Kinexon data. Existing reports and local analysis remain available offline. No jobs run while the laptop is shut down.</p></div>
  </>;
}

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: false }; }
  static getDerivedStateFromError() { return { error: true }; }
  render() {
    if (this.state.error) return <main className="fatal-error"><TriangleAlert size={32} /><h1>The view could not be loaded.</h1><p>Your stored data has not been changed. Reload the app to try again.</p><button className="button primary" onClick={() => window.location.reload()}>Reload application</button></main>;
    return this.props.children;
  }
}

// Vite can re-evaluate the entry module during development. Reuse this DOM
// container's root so edits never attach duplicate React roots or listeners.
renderApp(document.getElementById('root'), <ErrorBoundary><App /></ErrorBoundary>, createRoot);
