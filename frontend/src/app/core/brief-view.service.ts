import { Injectable, signal } from '@angular/core';

/** Which Brief cards are expanded. In memory only: kept while navigating, reset on reload. */
@Injectable({ providedIn: 'root' })
export class BriefViewService {
  private readonly expandedIds = signal<ReadonlySet<number>>(new Set());

  isExpanded(id: number): boolean {
    return this.expandedIds().has(id);
  }

  set(id: number, open: boolean): void {
    this.expandedIds.update((ids) => {
      const next = new Set(ids);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  allExpanded(ids: readonly number[]): boolean {
    const open = this.expandedIds();
    return ids.length > 0 && ids.every((id) => open.has(id));
  }

  setAll(ids: readonly number[], open: boolean): void {
    this.expandedIds.update((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (open) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }
}
