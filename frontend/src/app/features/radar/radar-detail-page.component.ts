import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import type { RadarCandidateDetail } from '../../api/models';
import { ApiService } from '../../core/api.service';
import {
  dismissReasonLabel,
  kindLabel,
  originLabel,
  radarStatusInfo,
  relationInfo,
  segmentLabel,
  shortDate,
} from '../../core/format';
import { type Cite, CitationNumbers, CitesComponent } from '../../shared/cites.component';
import { ScoreBarComponent } from '../../shared/score-bar.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { type CandidatePatch, RadarActionsService } from './radar-actions.service';
import type { RadarAction } from './radar-row.component';

type State = { status: 'loading' } | { status: 'error'; notFound: boolean } | { status: 'ready' };

const STATEMENT_LABELS: Record<string, string> = {
  what_it_is: 'What it is',
  segments: 'Segments',
  jfrog_relation: 'JFrog relation',
  stage: 'Stage',
  why_notable_now: 'Why notable now',
};

const STAGES: Record<string, string> = {
  open_source_project: 'Open-source project',
  startup: 'Startup',
  feature_of_large_company: 'Feature of a large company',
  unknown: 'Unknown',
};

/** A radar candidate: verified profile with numbered citations, mentions, metrics, score in words, merges, actions. */
@Component({
  selector: 'fr-radar-detail-page',
  standalone: true,
  imports: [CitesComponent, EmptyStateComponent, ErrorStateComponent, RouterLink, ScoreBarComponent, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './radar-detail-page.component.html',
  styleUrl: './radar-detail-page.component.scss',
})
export class RadarDetailPageComponent {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly actions = inject(RadarActionsService);

  private readonly id = toSignal(this.route.paramMap.pipe(map((p) => Number(p.get('id')))), { initialValue: NaN });
  private readonly reload = signal(0);
  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly c = signal<RadarCandidateDetail | null>(null);

  constructor() {
    toObservable(computed(() => ({ id: this.id(), n: this.reload() })))
      .pipe(
        switchMap(({ id }) =>
          this.api.radarCandidate(id).pipe(
            map((c) => ({ state: { status: 'ready' } as State, c })),
            catchError((e) => of({ state: { status: 'error', notFound: e?.status === 404 } as State, c: null })),
            startWith({ state: { status: 'loading' } as State, c: null }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe(({ state, c }) => {
        this.state.set(state);
        this.c.set(c);
      });
  }

  protected readonly kind = computed(() => kindLabel(this.c()?.kind ?? ''));
  protected readonly status = computed(() => radarStatusInfo(this.c()?.status ?? ''));
  protected readonly relation = computed(() => relationInfo(this.c()?.relation));
  protected readonly open = computed(() => ['new', 'watching'].includes(this.c()?.status ?? ''));
  protected readonly dismissReason = computed(() => dismissReasonLabel(this.c()?.dismiss_reason));

  /** Profile statements with citation numbers in order of first use. */
  protected readonly statements = computed(() => {
    const c = this.c();
    if (!c) return [];
    const numbers = new CitationNumbers();
    return c.statements.map((s) => ({
      key: s.key,
      label: STATEMENT_LABELS[s.key] ?? s.key,
      text: s.text,
      cites: numbers.all(s.citations) as Cite[],
      tags: [
        ...(s.segments ?? []).map(segmentLabel),
        ...(s.relation ? [relationInfo(s.relation).label] : []),
        ...(s.products ?? []),
        ...(s.stage ? [STAGES[s.stage] ?? s.stage] : []),
      ],
    }));
  });

  protected readonly mentions = computed(() =>
    (this.c()?.mentions ?? []).map((m) => ({ ...m, date: shortDate(m.published_at), originText: originLabel(m.origin) })),
  );

  protected readonly metrics = computed(() => {
    const c = this.c();
    if (!c) return [];
    const m = c.metrics;
    const rows: { label: string; value: string }[] = [
      { label: 'Mentions', value: `${m.mentions} (${m.mentions_recent} in the last 14 days)` },
      { label: 'Sources', value: String(m.sources) },
    ];
    if (m.stars != null) rows.push({ label: 'GitHub stars', value: m.stars.toLocaleString('en-GB') });
    if (m.star_velocity != null) {
      const basis = m.star_velocity_basis === 'snapshots' ? 'measured between runs' : 'stars ÷ repo age';
      rows.push({ label: 'Star velocity', value: `${m.star_velocity} per day (${basis})` });
    }
    if (m.hn_points_max != null) rows.push({ label: 'Best Hacker News post', value: `${m.hn_points_max} points` });
    if (m.max_impact != null) rows.push({ label: 'Highest mention impact', value: `${m.max_impact} of 5` });
    rows.push({ label: 'First seen', value: shortDate(c.first_seen) });
    rows.push({ label: 'Last seen', value: shortDate(c.last_seen) });
    return rows;
  });

  protected readonly merges = computed(() =>
    (this.c()?.merges ?? []).map((m) => ({
      ...m,
      text:
        m.direction === 'absorbed'
          ? `Merged in “${m.name}”`
          : m.direction === 'merged_into'
            ? `Merged into “${m.name}”`
            : `Also known as “${m.name}”`,
      rule: m.rule.replace(/_/g, ' '),
      date: m.at ? shortDate(m.at) : '',
    })),
  );

  protected readonly profileDate = computed(() => shortDate(this.c()?.profile_generated_at));

  retry(): void {
    this.reload.update((n) => n + 1);
  }

  act(action: RadarAction): void {
    const c = this.c();
    if (!c) return;
    const apply = (patch: CandidatePatch) => this.c.update((x) => (x ? { ...x, ...patch } : x));
    this.actions[action](c, apply, () => document.querySelector<HTMLElement>('#candidate-title'));
  }
}
