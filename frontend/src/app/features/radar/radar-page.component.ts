import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import type { RadarCandidate, RadarList } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { plural } from '../../core/format';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { type CandidatePatch, RadarActionsService } from './radar-actions.service';
import { KINDS, RELATIONS, STATUSES, type RadarFilters, parseRadarFilters, radarQueryParams, toggle } from './radar-filters';
import { type RadarAction, RadarRowComponent } from './radar-row.component';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; list: RadarList };

/**
 * Competitor Radar: players nobody configured, ranked by the emergence score. Lower-relevance
 * candidates (below the configured score, or no JFrog overlap) are collapsed at the end.
 */
@Component({
  selector: 'fr-radar-page',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, RadarRowComponent, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './radar-page.component.html',
  styleUrl: './radar-page.component.scss',
})
export class RadarPageComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly actions = inject(RadarActionsService);

  protected readonly statuses = STATUSES;
  protected readonly relations = RELATIONS;
  protected readonly kinds = KINDS;
  protected readonly filters = toSignal(this.route.queryParams.pipe(map(parseRadarFilters)), {
    initialValue: parseRadarFilters({}),
  });
  private readonly reload = signal(0);
  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly items = signal<RadarCandidate[]>([]);
  protected readonly filtersOpen = signal(false);

  constructor() {
    toObservable(computed(() => ({ f: this.filters(), n: this.reload() })))
      .pipe(
        switchMap(({ f }) =>
          this.api
            .radar({ status: f.status.join(','), relation: f.relation.join(',') || undefined, kind: f.kind || undefined, limit: 200 })
            .pipe(
              map((list): State => ({ status: 'ready', list })),
              catchError(() => of<State>({ status: 'error' })),
              startWith<State>({ status: 'loading' }),
            ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((s) => {
        this.state.set(s);
        if (s.status === 'ready') this.items.set(s.list.items);
      });
  }

  private readonly threshold = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.list.lower_relevance_below : 0;
  });
  private isLower = (c: RadarCandidate) => c.relation === 'none' || (c.score ?? 0) < this.threshold();
  protected readonly top = computed(() => this.items().filter((c) => !this.isLower(c)));
  protected readonly lower = computed(() => this.items().filter((c) => this.isLower(c)));
  protected readonly thresholdText = computed(() => Math.round(this.threshold()));
  protected readonly summary = computed(() => {
    const s = this.state();
    if (s.status !== 'ready') return '';
    const shown = s.list.items.length;
    return s.list.total > shown ? `Top ${shown} of ${plural(s.list.total, 'candidate')}` : plural(shown, 'candidate');
  });
  protected readonly activeFilters = computed(() => {
    const f = this.filters();
    return (radarQueryParams(f)['status'] ? 1 : 0) + f.relation.length + (f.kind ? 1 : 0);
  });

  setStatus(value: string, on: boolean): void {
    const status = toggle(this.filters().status, value, on);
    this.navigate({ ...this.filters(), status: status.length ? status : ['new', 'watching'] });
  }

  setRelation(value: string, on: boolean): void {
    this.navigate({ ...this.filters(), relation: toggle(this.filters().relation, value, on) });
  }

  setKind(value: string): void {
    this.navigate({ ...this.filters(), kind: value });
  }

  clear(): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: {} });
  }

  retry(): void {
    this.reload.update((n) => n + 1);
  }

  onAction(c: RadarCandidate, action: RadarAction): void {
    const apply = (patch: CandidatePatch) =>
      this.items.update((list) => list.map((x) => (x.id === c.id ? { ...x, ...patch } : x)));
    const current = this.items().find((x) => x.id === c.id) ?? c;
    this.actions[action](current, apply, () => document.querySelector<HTMLElement>(`#cand-${c.id} a`));
  }

  private navigate(f: RadarFilters): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: radarQueryParams(f) });
  }
}
