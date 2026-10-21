import { Injectable, inject, signal } from '@angular/core';

import type { NavCounts } from '../api/models';
import { ApiService } from './api.service';

/** Sidebar counts. Loaded once; pages call refresh() after an action changes them. */
@Injectable({ providedIn: 'root' })
export class NavCountsService {
  private readonly api = inject(ApiService);
  readonly counts = signal<NavCounts | null>(null);

  refresh(): void {
    this.api.nav().subscribe({
      next: (c) => this.counts.set(c),
      error: () => undefined, // keep the last known counts
    });
  }
}
