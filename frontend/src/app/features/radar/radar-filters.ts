import type { Params } from '@angular/router';

export const STATUSES = [
  { value: 'new', label: 'New' },
  { value: 'watching', label: 'Watching' },
  { value: 'dismissed', label: 'Dismissed' },
];
export const RELATIONS = [
  { value: 'threatens', label: 'Threatens' },
  { value: 'both', label: 'Both' },
  { value: 'complements', label: 'Complements' },
  { value: 'none', label: 'None' },
];
export const KINDS = [
  { value: 'company', label: 'Company' },
  { value: 'product', label: 'Product' },
  { value: 'open_source_project', label: 'Open-source project' },
];
export const DEFAULT_STATUS = ['new', 'watching'];

export interface RadarFilters {
  status: string[];
  relation: string[];
  kind: string;
}

function csv(v: unknown, allowed: { value: string }[]): string[] {
  if (typeof v !== 'string') return [];
  const ok = new Set(allowed.map((a) => a.value));
  return v.split(',').filter((x) => ok.has(x));
}

/** URL → filters (unknown values dropped; no status = new + watching). */
export function parseRadarFilters(p: Params): RadarFilters {
  const status = csv(p['status'], STATUSES);
  const kind = csv(p['kind'], KINDS)[0] ?? '';
  return { status: status.length ? status : DEFAULT_STATUS, relation: csv(p['relation'], RELATIONS), kind };
}

export function radarQueryParams(f: RadarFilters): Params {
  const isDefault = f.status.length === DEFAULT_STATUS.length && DEFAULT_STATUS.every((s) => f.status.includes(s));
  return {
    status: isDefault ? null : f.status.join(','),
    relation: f.relation.length ? f.relation.join(',') : null,
    kind: f.kind || null,
  };
}

export function toggle(list: string[], value: string, on: boolean): string[] {
  const set = new Set(list);
  if (on) set.add(value);
  else set.delete(value);
  return [...set];
}
