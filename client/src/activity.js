export function activityLabel(session = {}) {
  if (session.activity_label) return session.activity_label;
  if (session.classification === "practice") return "Practice";
  if (session.classification === "game") {
    return session.classification_origin === "manual" || session.reviewed
      ? "Game"
      : "Game / scrimmage";
  }
  const labels = Array.isArray(session.source_labels)
    ? session.source_labels.join(" ")
    : String(session.source_labels || "");
  return /\b(?:practice|training|shootaround)\b/i.test(labels) &&
    /\b(?:game|match)\b/i.test(labels)
    ? "Mixed recording"
    : "Unlabeled";
}

export function practiceComparisonExclusion(session = {}) {
  if (session.comparison_exclusion == null) return null;
  const reasons = {
    schedule_conflict:
      "the recording overlaps a conservative scheduled-game window",
    source_conflict: "its source activity labels conflict",
    invalid_bounds:
      "its recording times are missing, invalid or not yet complete",
    removed_upstream:
      "the recording was not returned by the latest source sync",
    not_practice: "the recording is not labeled as practice",
  };
  return (
    reasons[session.comparison_exclusion] ||
    "the recording is not eligible for a like-for-like practice comparison"
  );
}

export function comparisonStatus(player = {}) {
  const baseline = player.baseline || {};
  const reasons = {
    invalid_bounds: "Recording timing unavailable",
    removed_upstream: "Excluded source recording",
    schedule_conflict: "Scheduled-game overlap",
    source_conflict: "Mixed source labels",
    legacy_current: "Historical measurement limits",
    unknown_exposure_basis: "Exposure definition unknown",
    missing_current_measurements: "Current rate unavailable",
    nonpositive_current_exposure: "No positive exposure",
    measurement_out_of_range: "Measurement outside valid range",
    zero_baseline: "Prior average is zero",
  };
  if (reasons[baseline.reason_code]) return reasons[baseline.reason_code];
  if (player.metrics?.load_per_minute == null)
    return "Current rate unavailable";
  if (baseline.change_pct != null) return "Comparable";
  if (
    baseline.reason_code?.includes("exposure") ||
    baseline.reason_code?.includes("denominator")
  )
    return "Exposure not comparable";
  if (baseline.reason_code?.includes("legacy"))
    return "Historical measurement limits";
  if (baseline.reason_code?.includes("bounds"))
    return "Recording timing unavailable";
  if (baseline.load_per_minute === 0) return "Prior average is zero";
  return `${baseline.sample_count || 0} of ${baseline.min_samples || 3} prior practices`;
}

export function practicePoints(player = {}, session = {}) {
  const baseline = player.baseline || {};
  const history = Array.isArray(baseline.history) ? baseline.history : [];
  const prior = history
    .filter((point) => point.session_id !== session.id)
    .map((point) => ({ ...point, current: false }))
    .sort(
      (left, right) =>
        String(left.start || left.date).localeCompare(
          String(right.start || right.date),
        ) || Number(left.session_id) - Number(right.session_id),
    );
  if (
    Object.hasOwn(baseline, "current_point") &&
    baseline.current_point === null
  )
    return prior;
  const current = baseline.current_point || {
    session_id: session.id,
    date: session.date || session.local_date,
    start: session.start,
    load_per_minute: player.metrics?.load_per_minute ?? null,
    minutes: player.metrics?.minutes ?? null,
    mechanical_load: player.metrics?.mechanical_load ?? null,
  };
  return [...prior, { ...current, current: true }];
}

export function plotPoints(points, width = 660, height = 240) {
  const known = points.filter(
    (point) =>
      typeof point.load_per_minute === "number" &&
      Number.isFinite(point.load_per_minute) &&
      point.load_per_minute >= 0,
  );
  const maximum =
    Math.max(1, ...known.map((point) => point.load_per_minute)) * 1.15;
  const top = 22,
    bottom = height - 38,
    left = 54,
    right = width - 26;
  const plotted = points.map((point, index) => ({
    ...point,
    x:
      points.length > 1
        ? left + ((right - left) * index) / (points.length - 1)
        : (left + right) / 2,
    y: known.includes(point)
      ? bottom - (point.load_per_minute / maximum) * (bottom - top)
      : null,
  }));
  const segments = [];
  let segment = [];
  for (const point of plotted) {
    if (point.y === null) {
      if (segment.length) segments.push(segment);
      segment = [];
    } else segment.push(point);
  }
  if (segment.length) segments.push(segment);
  return { points: plotted, segments, maximum, top, bottom, left, right };
}
