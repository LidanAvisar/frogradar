import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import type { Signal, SignalPage } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { BriefViewService } from '../../core/brief-view.service';
import { CATEGORY_LABELS, plural } from '../../core/format';
import { IconComponent } from '../../shared/icon.component';
import { type SignalCardAction, SignalCardComponent } from '../../shared/signal-card.component';
import { SignalActionsService, SignalOverlay } from '../review/signal-actions.service';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import {
  type SignalFilters,
  WINDOWS,
  WINDOW_LABELS,
  activeCount,
  parseFilters,
  toApiQuery,
  toQueryParams,
} from './signal-filters';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; page: SignalPage };

export const PAGE_SIZE = 20;

/**
 * Every visible signal, filtered and searchable. All filters live in the URL query string, so the
 * Brief tiles and search open it pre-filtered and Back restores the previous view.
 */
@Component({
  selector: 'fr-signals-page',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, IconComponent, SignalCardComponent, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './signals-page.component.html',
  styleUrl: './signals-page.component.scss',
})
export class SignalsPageComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected readonly view = inject(BriefViewService);
  private readonly resultsHeading = viewChild<ElementRef<HTMLElement>>('resultsHeading');

  protected readonly filters = toSignal(this.route.queryParams.pipe(map(parseFilters)), {
    initialValue: parseFilters({}),
  });
  private readonly reload = signal(0);
  protected readonly state = toSignal(
    toObservable(computed(() => ({ f: this.filters(), n: this.reload() }))).pipe(
      switchMap(({ f }) =>
        this.api.signals(toApiQuery(f, PAGE_SIZE)).pipe(
          map((page): State => ({ status: 'ready', page })),
          catchError(() => of<State>({ status: 'error' })),
          startWith<State>({ status: 'loading' }),
        ),
      ),
    ),
    { initialValue: { status: 'loading' } as State },
  );
  protected readonly companies = toSignal(
    this.api.entities().pipe(
      map((list) => list.filter((e) => !e.is_self)),
      catchError(() => of([])),
    ),
    { initialValue: [] },
  );

  protected readonly windows = WINDOWS.map((w) => ({ value: w, label: WINDOW_LABELS[w] }));
  protected readonly categories = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label }));
  protected readonly impacts = [5, 4, 3, 2];
  protected readonly filtersOpen = signal(false);
  protected readonly active = computed(() => activeCount(this.filters()));

  protected readonly page = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.page : null;
  });
  private readonly actions = inject(SignalActionsService);
  /** Analyst actions on top of the loaded page (optimistic; a rejected card disappears). */
  private readonly overlay = new SignalOverlay();
  protected readonly items = computed(() => this.overlay.view(this.page()?.items ?? []));
  protected readonly ids = computed(() => this.items().map((s) => s.id));
  protected readonly allExpanded = computed(() => this.view.allExpanded(this.ids()));
  protected readonly pages = computed(() => {
    const p = this.page();
    return p ? Math.max(1, Math.ceil(p.total / p.page_size)) : 1;
  });
  protected readonly summary = computed(() => {
    const p = this.page();
    if (!p) return '';
    if (p.total === 0) return 'No signals match';
    const from = (p.page - 1) * p.page_size + 1;
    const to = from + p.items.length - 1;
    return p.total <= p.page_size ? plural(p.total, 'signal') : `${from}–${to} of ${plural(p.total, 'signal')}`;
  });
  protected readonly skeletons = [1, 2, 3, 4];

  onAct(action: SignalCardAction, s: Signal): void {
    this.actions.act(action, s, this.overlay.applier(s.id), () => this.resultsHeading()?.nativeElement ?? null);
  }

  set(patch: Partial<SignalFilters>, resetPage = true): void {
    const next = { ...this.filters(), ...patch, ...(resetPage ? { page: 1 } : {}) };
    this.router.navigate([], { relativeTo: this.route, queryParams: toQueryParams(next) });
  }

  search(event: Event, value: string): void {
    event.preventDefault();
    this.set({ q: value.trim() });
  }

  onSelect(key: 'entity' | 'category', value: string): void {
    this.set({ [key]: value });
  }

  onImpact(value: string): void {
    this.set({ minImpact: value ? Number(value) : null });
  }

  onWindow(value: string): void {
    this.set({ since: (WINDOWS.find((w) => w === value) ?? null) });
  }

  onKind(value: string): void {
    this.set({ kind: value === 'article' || value === 'page_diff' ? value : null });
  }

  clear(): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: {} });
  }

  goTo(page: number): void {
    this.set({ page }, false);
    this.resultsHeading()?.nativeElement.focus();
    globalThis.scrollTo?.({ top: 0 });
  }

  retry(): void {
    this.reload.update((n) => n + 1);
  }

  toggleAll(): void {
    this.view.setAll(this.ids(), !this.allExpanded());
  }
}
