import test from "node:test";
import assert from "node:assert/strict";
import {
  activityLabel,
  comparisonStatus,
  plotPoints,
  practicePoints,
} from "../src/activity.js";

test("activity badges use simple primary labels and prefer API descriptions", () => {
  assert.equal(
    activityLabel({ classification: "practice", reviewed: false }),
    "Practice",
  );
  assert.equal(
    activityLabel({ classification: "game", reviewed: false }),
    "Game / scrimmage",
  );
  assert.equal(
    activityLabel({ classification: "game", classification_origin: "manual" }),
    "Game",
  );
  assert.equal(
    activityLabel({
      classification: "unknown",
      activity_label: "Mixed recording",
    }),
    "Mixed recording",
  );
  assert.equal(
    activityLabel({
      classification: "practice",
      activity_label: "Practice",
    }).includes("review"),
    false,
  );
});

test("unknown activity does not fabricate a clean practice or official game", () => {
  assert.equal(
    activityLabel({
      classification: "unknown",
      source_labels: ["Training", "Match"],
    }),
    "Mixed recording",
  );
  assert.equal(
    activityLabel({
      classification: "unknown",
      source_labels: ["Shootaround", "Game"],
    }),
    "Mixed recording",
  );
  assert.equal(
    activityLabel({ classification: "unknown", source_labels: [] }),
    "Unlabeled",
  );
  assert.equal(activityLabel(), "Unlabeled");
});

test("comparison status distinguishes missing current measurements, small samples and zero references", () => {
  assert.equal(
    comparisonStatus({ metrics: {}, baseline: { sample_count: 5 } }),
    "Current rate unavailable",
  );
  assert.equal(
    comparisonStatus({
      metrics: { load_per_minute: 10 },
      baseline: { sample_count: 2, min_samples: 3 },
    }),
    "2 of 3 prior practices",
  );
  assert.equal(
    comparisonStatus({
      metrics: { load_per_minute: 10 },
      baseline: { sample_count: 5, load_per_minute: 0 },
    }),
    "Prior average is zero",
  );
  assert.equal(
    comparisonStatus({
      metrics: { load_per_minute: 0 },
      baseline: { change_pct: 0 },
    }),
    "Comparable",
  );
  assert.equal(
    comparisonStatus({
      metrics: { load_per_minute: 10 },
      baseline: { reason_code: "exposure_mismatch" },
    }),
    "Exposure not comparable",
  );
});

test("history is chronological without modifying source records, with current recording last", () => {
  const history = [
    { session_id: 2, date: "2026-10-08", load_per_minute: 12 },
    { session_id: 1, date: "2026-10-01", load_per_minute: 10 },
  ];
  const player = {
    baseline: { history },
    metrics: { load_per_minute: 11, minutes: 30, mechanical_load: 330 },
  };
  const points = practicePoints(player, { id: 3, date: "2026-10-09" });
  assert.deepEqual(
    points.map((point) => point.session_id),
    [1, 2, 3],
  );
  assert.deepEqual(
    history.map((point) => point.session_id),
    [2, 1],
  );
  assert.deepEqual(
    points.map((point) => point.current),
    [false, false, true],
  );
  assert.equal(points[2].load_per_minute, 11);
  assert.equal(points[2].minutes, 30);
});

test("API current-point values are retained and not reconstructed from mismatched records", () => {
  const point = {
    session_id: 3,
    date: "2026-10-09",
    load_per_minute: null,
    minutes: null,
    mechanical_load: 20,
  };
  const points = practicePoints(
    { baseline: { current_point: point }, metrics: { load_per_minute: 99 } },
    { id: 3 },
  );
  assert.equal(points[0].load_per_minute, null);
  assert.equal(points[0].mechanical_load, 20);
  const excluded = practicePoints(
    {
      baseline: { current_point: null, reason_code: "legacy_current" },
      metrics: { load_per_minute: 99 },
    },
    { id: 3 },
  );
  assert.equal(
    excluded.length,
    0,
    "an explicit comparison exclusion must not be plotted using raw report metrics",
  );
});

test("unknown rates stay missing while real zero values receive plotted points", () => {
  const result = plotPoints([
    { load_per_minute: 0 },
    { load_per_minute: null },
    { load_per_minute: -1 },
    { load_per_minute: NaN },
  ]);
  assert.equal(result.points[0].y, result.bottom);
  assert.deepEqual(
    result.points.slice(1).map((point) => point.y),
    [null, null, null],
  );
  assert.equal(result.segments.length, 1);
  assert.equal(result.segments[0].length, 1);
});

test("chart breaks across missing points instead of fabricating connected measurements", () => {
  const result = plotPoints([
    { load_per_minute: 10 },
    { load_per_minute: 15 },
    { load_per_minute: null },
    { load_per_minute: 30 },
  ]);
  assert.deepEqual(
    result.segments.map((segment) => segment.length),
    [2, 1],
  );
  assert.ok(result.points[0].x < result.points[1].x);
  assert.ok(result.points[1].x < result.points[3].x);
  assert.ok(result.points[3].y >= result.top);
});

test("single and empty series are stable and contain no invalid chart coordinates", () => {
  const single = plotPoints([{ load_per_minute: 0 }]);
  assert.equal(single.points[0].x, (single.left + single.right) / 2);
  assert.ok(Number.isFinite(single.points[0].y));
  assert.deepEqual(plotPoints([]).segments, []);
  assert.deepEqual(practicePoints({}, { id: 1 })[0].load_per_minute, null);
});
