import { Injectable, computed, inject, signal } from '@angular/core';

import type { SystemStatus } from '../api/models';
import { ApiService } from './api.service';

/**
 * Keys present and features, today's LLM spend (GET /api/status). Loaded once by the
 * shell; pages that spend money call refresh() afterwards.
 */
@Injectable({ providedIn: 'root' })
export class StatusService {
  private readonly api = inject(ApiService);
  readonly status = signal<SystemStatus | null>(null);

  readonly costs = computed(() => this.status()?.cost_estimates_usd ?? null);

  refresh(): void {
    this.api.status().subscribe({
      next: (s) => this.status.set(s),
      error: () => undefined, // keep the last known status
    });
  }

  featureState(name: string): string | null {
    return this.feature(name)?.state ?? null;
  }

  /** A feature with its state and the reason it is off or degraded (e.g. a missing optional key). */
  feature(name: string): SystemStatus['features'][number] | null {
    return this.status()?.features.find((f) => f.name === name) ?? null;
  }
}

/** "$0.02" for small amounts, "$1.50" otherwise. */
export function usdShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return '–';
  return v > 0 && v < 0.01 ? '<$0.01' : `$${v.toFixed(2)}`;
}
