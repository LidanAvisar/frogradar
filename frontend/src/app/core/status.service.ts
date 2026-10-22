import { Injectable, computed, inject, signal } from '@angular/core';

import type { SystemStatus } from '../api/models';
import { ApiService } from './api.service';

/**
 * Mode (demo / live), keys and features, today's LLM spend (GET /api/status). Loaded once by the
 * shell; pages that spend money call refresh() afterwards.
 */
@Injectable({ providedIn: 'root' })
export class StatusService {
  private readonly api = inject(ApiService);
  readonly status = signal<SystemStatus | null>(null);

  readonly demo = computed(() => this.status()?.mode === 'demo');
  readonly asOf = computed(() => this.status()?.as_of ?? null);
  readonly demoQuestions = computed(() => this.status()?.demo_questions ?? []);
  readonly costs = computed(() => this.status()?.cost_estimates_usd ?? null);

  refresh(): void {
    this.api.status().subscribe({
      next: (s) => this.status.set(s),
      error: () => undefined, // keep the last known status
    });
  }

  featureState(name: string): string | null {
    return this.status()?.features.find((f) => f.name === name)?.state ?? null;
  }
}

/** "$0.02" for small amounts, "$1.50" otherwise. */
export function usdShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return '–';
  return v > 0 && v < 0.01 ? '<$0.01' : `$${v.toFixed(2)}`;
}

/** "21 Oct 2026" (UTC) for the snapshot's as-of time. */
export function asOfLabel(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(iso),
  );
}
