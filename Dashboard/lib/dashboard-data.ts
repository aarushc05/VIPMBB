export type Category = 'game' | 'practice' | 'other';
export type Segment = 'all' | Category;
export const metricInfo = {
  mechanical_load: { label: 'Mechanical load', unit: 'Kinexon units', scale: 1, decimals: 0 },
  distance_m: { label: 'Distance', unit: 'km', scale: 0.001, decimals: 2 },
  minutes: { label: 'Tracked exposure', unit: 'min', scale: 1, decimals: 0 },
  speed_max: { label: 'Peak speed', unit: 'm/s', scale: 1, decimals: 2 },
  jump_count: { label: 'Jumps', unit: 'jumps', scale: 1, decimals: 0 },
  high_intensity_actions: { label: 'Movement actions', unit: 'events', scale: 1, decimals: 0 },
  acceleration_count: { label: 'Accelerations', unit: 'events', scale: 1, decimals: 0 },
  deceleration_count: { label: 'Decelerations', unit: 'events', scale: 1, decimals: 0 },
  change_of_direction_count: { label: 'Changes of direction', unit: 'events', scale: 1, decimals: 0 },
  transition_count: { label: 'Court transitions', unit: 'events', scale: 1, decimals: 0 },
  physiological_load: { label: 'Physiological load', unit: 'Kinexon units', scale: 1, decimals: 0 },
  accel_load: { label: 'Acceleration load', unit: 'Kinexon units', scale: 1, decimals: 0 },
  metabolic_work: { label: 'Metabolic work', unit: 'kcal', scale: 1, decimals: 0 },
};
export type MetricKey = keyof typeof metricInfo;
export const metricKeys = Object.keys(metricInfo) as MetricKey[];
export type Metrics = Record<MetricKey, number> & { records: number; observed: Record<MetricKey, number> };
export function blankMetrics(): Metrics {
  return { ...Object.fromEntries(metricKeys.map(key => [key, 0])), records: 0,
    observed: Object.fromEntries(metricKeys.map(key => [key, 0])) } as Metrics;
}
export function mergeMetrics(target: Metrics, source: Metrics) {
  target.records += source.records;
  for (const key of metricKeys) {
    target[key] = key === 'speed_max' ? Math.max(target[key], source[key]) : target[key] + source[key];
    target.observed[key] += source.observed[key];
  }
}
export function metricValue(metrics: Metrics, key: MetricKey) {
  return metrics.observed[key] ? metrics[key] : null;
}
export function formatMetric(value: number | null, key: MetricKey) {
  if (value === null) return '—';
  const info = metricInfo[key];
  return (value * info.scale).toLocaleString('en-US', { maximumFractionDigits: info.decimals });
}
export type Player = {
  id: number; name: string; number: string; position: string; active: boolean;
  metrics: Record<Segment, Metrics>;
};
export type Session = {
  id: number; date: string; start: string; labels: string[]; category: Category; reason: string;
  metrics: Metrics; playerIds: number[]; assignedIds: number[]; assignmentsKnown: boolean; zeroRecords: number;
};
export type Cache = {
  players: Map<number, Player>; sessions: Session[]; records: Record<string, unknown>[];
  syncedAt: string | null; checkedThrough: string | null; legacy: boolean; syncStatus: string | null;
};
export type View = {
  players: Player[]; sessions: Session[]; totals: Metrics; byCategory: Record<Category, Metrics>;
  days: { date: string; game: number | null; practice: number | null; other: number | null }[];
  withStats: number; zeroRecords: number;
};
