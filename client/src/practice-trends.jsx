import React, { useState } from "react";
import { ArrowUpRight, BarChart3, ChevronDown } from "lucide-react";
import { comparisonStatus, plotPoints, practicePoints } from "./activity.js";
import { dateLabel, number, percentage } from "./format.js";

function exposureLabel(minutes) {
  if (
    minutes == null ||
    minutes === "" ||
    !Number.isFinite(Number(minutes)) ||
    Number(minutes) < 0
  )
    return "Exposure unavailable";
  const value = Number(minutes);
  return `${value > 0 && value < 0.01 ? "<0.01" : number(value, 2)} min recorded exposure`;
}

function historyDates(baseline) {
  const history = baseline?.history || [];
  if (!history.length) return "No comparable history";
  const first = dateLabel(history[0].date || history[0].start, {
    year: undefined,
  });
  const last = dateLabel(history.at(-1).date || history.at(-1).start, {
    year: undefined,
  });
  return first === last ? first : `${first} – ${last}`;
}

function TrendChart({ player, session }) {
  const points = practicePoints(player, session);
  const plot = plotPoints(points);
  const baseline = player.baseline || {};
  const average = baseline.load_per_minute;
  const averageY =
    typeof average === "number" && Number.isFinite(average)
      ? plot.bottom - (average / plot.maximum) * (plot.bottom - plot.top)
      : null;
  const available = plot.points.filter((point) => point.y !== null);
  return (
    <div className="practice-trend-chart">
      <div className="trend-selected-summary">
        <div>
          <span>This practice</span>
          <strong>
            {number(player.metrics?.load_per_minute, 2)}
            <small>AU/min</small>
          </strong>
          <small>{exposureLabel(player.metrics?.minutes)}</small>
        </div>
        <div>
          <span>Recent weighted average</span>
          <strong>
            {number(average, 2)}
            <small>AU/min</small>
          </strong>
        </div>
        <div>
          <span>Difference</span>
          <strong>{percentage(baseline.change_pct)}</strong>
          <small>{comparisonStatus(player)}</small>
        </div>
      </div>
      {available.length ? (
        <figure className="trend-figure">
          <svg
            viewBox="0 0 660 240"
            role="group"
            aria-label={`${player.name}: load per minute across recorded practices`}
          >
            <title>{player.name}: recorded practice trend</title>
            <desc>
              Previous comparable practices followed by this recording. Points
              are evenly spaced by recording, not elapsed time. Every point
              links to its source report. Exact dates and measurements are
              available in the table below.
            </desc>
            {[0, 0.5, 1].map((fraction) => {
              const y = plot.bottom - fraction * (plot.bottom - plot.top);
              return (
                <g key={fraction}>
                  <line
                    x1={plot.left}
                    x2={plot.right}
                    y1={y}
                    y2={y}
                    className="trend-grid-line"
                  />
                  <text
                    x={plot.left - 10}
                    y={y + 4}
                    textAnchor="end"
                    className="trend-axis-label"
                  >
                    {number(plot.maximum * fraction, 1)}
                  </text>
                </g>
              );
            })}
            {averageY !== null && (
              <line
                x1={plot.left}
                x2={plot.right}
                y1={averageY}
                y2={averageY}
                className="trend-average-line"
              />
            )}
            {plot.segments
              .filter((segment) => segment.length > 1)
              .map((segment, index) => (
                <polyline
                  key={index}
                  points={segment
                    .map((point) => `${point.x},${point.y}`)
                    .join(" ")}
                  className="trend-line"
                />
              ))}
            {plot.points.map((point, index) => (
              <g key={`${point.session_id}-${index}`}>
                {point.y !== null && (
                  <a
                    href={`#report/${point.session_id}`}
                    aria-label={`Open ${point.current ? "current " : ""}practice report for ${dateLabel(point.date || point.start)}: ${number(point.load_per_minute, 2)} AU per minute`}
                  >
                    <circle
                      cx={point.x}
                      cy={point.y}
                      r="16"
                      className="trend-hit-area"
                    />
                    <circle
                      cx={point.x}
                      cy={point.y}
                      r={point.current ? 6 : 5}
                      className={
                        point.current ? "trend-point current" : "trend-point"
                      }
                    />
                    <title>
                      {dateLabel(point.date || point.start)} ·{" "}
                      {number(point.load_per_minute, 2)} AU/min
                      {point.current ? " · This practice" : ""}
                    </title>
                  </a>
                )}
                <text
                  x={point.x}
                  y="229"
                  textAnchor={
                    index === 0 && plot.points.length > 1
                      ? "start"
                      : index === plot.points.length - 1 &&
                          plot.points.length > 1
                        ? "end"
                        : "middle"
                  }
                  className="trend-axis-label"
                >
                  {dateLabel(point.date || point.start, { year: undefined })}
                </text>
              </g>
            ))}
          </svg>
          <figcaption>
            <span>
              <i className="trend-legend-dot" />
              Prior practice
            </span>
            <span>
              <i className="trend-legend-dot current" />
              This practice
            </span>
            {averageY !== null && (
              <span>
                <i className="trend-legend-line" />
                Weighted average
              </span>
            )}
            <span>Equal spacing by recording · AU/min</span>
          </figcaption>
        </figure>
      ) : (
        <div className="trend-no-measurements">
          <BarChart3 size={24} />
          <p>
            {baseline.reason ||
              "Load per minute is unavailable for this player in the selected records."}
          </p>
        </div>
      )}
      {points.length > 0 && (
        <details className="trend-source-details">
          <summary>
            Dates and source measurements{" "}
            <span>
              {points.length} {points.length === 1 ? "recording" : "recordings"}
            </span>
            <ChevronDown size={15} />
          </summary>
          <div className="table-scroll">
            <table className="trend-source-table">
              <caption className="sr-only">
                Source measurements for {player.name}
              </caption>
              <thead>
                <tr>
                  <th>Practice</th>
                  <th className="numeric">
                    Load / minute<small>AU/min</small>
                  </th>
                  <th className="numeric">
                    Exposure<small>min</small>
                  </th>
                  <th className="numeric">
                    Load<small>AU</small>
                  </th>
                </tr>
              </thead>
              <tbody>
                {points.map((point, index) => (
                  <tr
                    key={`${point.session_id}-${index}`}
                    className={point.current ? "trend-current-row" : undefined}
                  >
                    <td>
                      <a
                        href={`#report/${point.session_id}`}
                        className="trend-source-link"
                      >
                        {dateLabel(point.date || point.start)}
                        <ArrowUpRight size={13} />
                      </a>
                      {point.current && (
                        <span className="trend-row-note">This practice</span>
                      )}
                    </td>
                    <td className="numeric">
                      {number(point.load_per_minute, 2)}
                    </td>
                    <td className="numeric">{number(point.minutes, 1)}</td>
                    <td className="numeric">
                      {number(point.mechanical_load, 1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

export function PracticeTrends({ report }) {
  const players = report.players || [];
  const [selectedId, setSelectedId] = useState(null);
  const selected =
    players.find((player) => String(player.id) === selectedId) ||
    players.find((player) => player.baseline?.change_pct != null) ||
    players[0];
  const defaults = selected?.baseline || {};
  return (
    <section className="panel practice-trends" data-reveal>
      <header className="panel-heading">
        <div>
          <h2>Recent practice trends</h2>
          <p>Separate time on the floor from workload per minute.</p>
        </div>
        {players.length > 0 && (
          <label className="trend-player-select">
            Player
            <select
              value={selected ? String(selected.id) : ""}
              onChange={(event) => setSelectedId(event.target.value)}
              aria-label="Player for recent practice trend"
            >
              {players.map((player) => (
                <option key={player.id} value={String(player.id)}>
                  {player.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>
      {selected ? (
        <TrendChart player={selected} session={report.session} />
      ) : (
        <p className="panel-padding muted">
          No player measurements are available for this practice yet.
        </p>
      )}
      {players.length > 0 && (
        <div className="table-scroll">
          <table className="practice-comparison-table">
            <caption className="sr-only">
              Current practice compared with each player's recent comparable
              practices
            </caption>
            <thead>
              <tr>
                <th>Player</th>
                <th className="numeric">
                  This practice<small>AU/min</small>
                </th>
                <th className="numeric">
                  Recent average<small>AU/min</small>
                </th>
                <th className="numeric">Difference</th>
                <th>Comparison records</th>
              </tr>
            </thead>
            <tbody>
              {players.map((player) => (
                <tr
                  key={player.id}
                  className={
                    player.id === selected?.id
                      ? "trend-selected-row"
                      : undefined
                  }
                >
                  <td>{player.name}</td>
                  <td className="numeric">
                    {number(player.metrics?.load_per_minute, 2)}
                    <span className="trend-row-note">
                      {exposureLabel(player.metrics?.minutes)}
                    </span>
                  </td>
                  <td className="numeric">
                    {number(player.baseline?.load_per_minute, 2)}
                  </td>
                  <td className="numeric">
                    {percentage(player.baseline?.change_pct)}
                  </td>
                  <td>
                    <span>
                      {player.baseline?.change_pct != null
                        ? `${player.baseline.sample_count} prior practices`
                        : comparisonStatus(player)}
                    </span>
                    <span className="trend-row-note">
                      {historyDates(player.baseline)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="table-footnote trend-methodology">
        Recent average = total load ÷ total exposure across up to{" "}
        {defaults.max_samples || 5} earlier comparable practices in the previous{" "}
        {defaults.lookback_days || 90} days and the same July–June season; at
        least {defaults.min_samples || 3} are needed. Only the same exposure
        measurement is compared. Current/future recordings, games and mixed
        activity are excluded from the reference. Missing measurements stay
        unavailable. Changes describe workload—not fatigue, readiness or effort.
      </p>
    </section>
  );
}
