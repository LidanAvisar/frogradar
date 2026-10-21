import type { Params } from '@angular/router';

import type { SignalQuery } from '../../core/api.service';

export const WINDOWS = ['24h', '7d', '30d', '90d'] as const;
export type Window = (typeof WINDOWS)[number];
export type Kind = 'article' | 'page_diff';

/** Signals page filters. The URL query string is the source of truth (shareable, Back works). */
export interface SignalFilters {
  q: string;
  entity: string;
  category: string;
  minImpact: number | null;
  since: Window | null;
  kind: Kind | null;
  page: number;
}

export const WINDOW_LABELS: Record<Window, string> = {
  '24h': 'Last 24 hours',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
  '90d': 'Last 90 days',
};

function int(v: unknown): number | null {
  const n = typeof v === 'string' ? Number.parseInt(v, 10) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** Parse query params; unknown values are ignored. `days=N` (older Brief links) maps to `since=Nd`. */
export function parseFilters(p: Params): SignalFilters {
  let since = WINDOWS.find((w) => w === p['since']) ?? null;
  if (!since && p['days']) {
    const d = int(p['days']);
    since = WINDOWS.find((w) => w === `${d}d`) ?? (d !== null && d <= 1 ? '24h' : null);
  }
  const impact = int(p['min_impact']);
  const page = int(p['page']);
  return {
    q: typeof p['q'] === 'string' ? p['q'] : '',
    entity: typeof p['entity'] === 'string' ? p['entity'] : '',
    category: typeof p['category'] === 'string' ? p['category'] : '',
    minImpact: impact !== null && impact >= 1 && impact <= 5 ? impact : null,
    since,
    kind: p['kind'] === 'article' || p['kind'] === 'page_diff' ? p['kind'] : null,
    page: page !== null && page >= 1 ? page : 1,
  };
}

/** Filters → query params for router.navigate (null removes a param). */
export function toQueryParams(f: SignalFilters): Params {
  return {
    q: f.q || null,
    entity: f.entity || null,
    category: f.category || null,
    min_impact: f.minImpact,
    since: f.since,
    kind: f.kind,
    page: f.page > 1 ? f.page : null,
    days: null,
  };
}

export function toApiQuery(f: SignalFilters, pageSize: number): SignalQuery {
  return {
    q: f.q || undefined,
    entity: f.entity || undefined,
    category: (f.category || undefined) as SignalQuery['category'],
    min_impact: f.minImpact ?? undefined,
    window: f.since ?? undefined,
    kind: f.kind ?? undefined,
    page: f.page,
    page_size: pageSize,
  };
}

export function activeCount(f: SignalFilters): number {
  return [f.q, f.entity, f.category, f.minImpact, f.since, f.kind].filter((v) => v !== '' && v !== null).length;
}
