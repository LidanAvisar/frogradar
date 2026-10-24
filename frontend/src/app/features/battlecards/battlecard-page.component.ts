import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { NgTemplateOutlet } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import type { Battlecard, CitedText, TimelineItem } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { categoryLabel, monthYear, plural, relationshipInfo, segmentLabel, shortDate } from '../../core/format';
import { type Cite, CitationNumbers, CitesComponent } from '../../shared/cites.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { CapabilityMatrixComponent } from './capability-matrix.component';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; card: Battlecard };

interface CitedItem {
  text: string;
  cites: Cite[];
}

const HIGH_IMPACT = 4;
const TIMELINE_SHOWN = 10;

/**
 * One competitor's battlecard (docs/design Battlecard): switcher, header, three tiles, capability
 * matrix (JFrog vs competitor), cited opportunities / blind spots / strengths / weaknesses and the
 * strategy timeline. Every card item cites the signals it is based on.
 */
@Component({
  selector: 'fr-battlecard-page',
  standalone: true,
  imports: [NgTemplateOutlet, CapabilityMatrixComponent, CitesComponent, EmptyStateComponent, ErrorStateComponent, RouterLink, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './battlecard-page.component.html',
  styleUrl: './battlecard-page.component.scss',
})
export class BattlecardPageComponent {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);

  protected readonly slug = toSignal(this.route.paramMap.pipe(map((p) => p.get('slug') ?? '')), {
    initialValue: '',
  });
  private readonly reload = signal(0);
  protected readonly competitors = toSignal(this.api.battlecards().pipe(catchError(() => of([]))), {
    initialValue: [],
  });
  protected readonly state = toSignal(
    toObservable(computed(() => ({ slug: this.slug(), n: this.reload() }))).pipe(
      switchMap(({ slug }) =>
        this.api.battlecard(slug).pipe(
          map((card): State => ({ status: 'ready', card })),
          catchError(() => of<State>({ status: 'error' })),
          startWith<State>({ status: 'loading' }),
        ),
      ),
    ),
    { initialValue: { status: 'loading' } as State },
  );

  protected readonly card = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.card : null;
  });
  protected readonly hasCard = computed(() => !!this.card()?.report_id);
  protected readonly name = computed(
    () => this.card()?.entity.name ?? this.competitors().find((c) => c.slug === this.slug())?.name ?? '',
  );
  protected readonly withCard = computed(() => this.competitors().filter((c) => c.generated_at));
  /** Enough evidence, card not generated yet (the next run builds it). */
  protected readonly pendingCard = computed(() => this.competitors().filter((c) => !c.generated_at && c.evidence.enough));
  /** Below the evidence threshold (D86): grouped under one label. */
  protected readonly thinEvidence = computed(() => this.competitors().filter((c) => !c.generated_at && !c.evidence.enough));
  protected readonly relationship = computed(() => relationshipInfo(this.card()?.entity.relationship));
  protected readonly segments = computed(() => (this.card()?.entity.segments ?? []).map(segmentLabel));
  protected readonly generated = computed(() => shortDate(this.card()?.generated_at));
  /** "1 of 5 signals where AWS is the main subject (last 180 days)" (D86). */
  protected readonly evidenceText = computed(() => {
    const c = this.card();
    if (!c) return '';
    const e = c.evidence;
    const signals = e.required === 1 ? 'signal' : 'signals';
    return `${e.signals_about} of ${e.required} ${signals} where ${c.entity.name} is the main subject (last ${e.lookback_days} days)`;
  });

  /** Citation numbers in reading order: positioning, opportunities, blind spots, strengths, weaknesses. */
  protected readonly sections = computed(() => {
    const card = this.card();
    if (!card) return null;
    const refs = new Map(card.citations.map((c) => [c.id, c]));
    const numbers = new CitationNumbers();
    const cite = (items: CitedText[]): CitedItem[] =>
      items.map((it) => ({
        text: it.text,
        cites: numbers.all(it.signal_ids.flatMap((id) => (refs.has(id) ? [refs.get(id)!] : []))),
      }));
    return {
      positioning: card.positioning ? cite([card.positioning])[0] : null,
      opportunities: cite(card.opportunities_for_jfrog),
      blindSpots: cite(card.blind_spots_for_jfrog),
      strengths: cite(card.strengths),
      weaknesses: cite(card.weaknesses),
    };
  });

  /** Newest high-impact signal (the timeline is newest first). */
  protected readonly latestMove = computed<TimelineItem | null>(
    () => this.card()?.timeline.find((t) => (t.impact ?? 0) >= HIGH_IMPACT) ?? null,
  );
  protected readonly coverage = computed(() => {
    const c = this.card()?.coverage;
    if (!c) return null;
    return {
      signals: plural(c.signals, 'signal'),
      high: plural(c.high_impact, 'high-impact signal'),
      sources: plural(c.sources, 'source'),
      caps: `${c.capabilities_known} of ${c.capabilities_total} capabilities known`,
      since: c.first_signal_at ? `since ${shortDate(c.first_signal_at)}` : '',
    };
  });

  protected readonly showAllTimeline = signal(false);
  protected readonly timeline = computed(() => {
    const all = this.card()?.timeline ?? [];
    return this.showAllTimeline() ? all : all.slice(0, TIMELINE_SHOWN);
  });
  protected readonly timelineTotal = computed(() => this.card()?.timeline.length ?? 0);
  protected readonly timelineCap = TIMELINE_SHOWN;

  protected readonly monthYear = monthYear;
  protected readonly shortDate = shortDate;
  protected readonly categoryLabel = categoryLabel;
  protected readonly highImpact = HIGH_IMPACT;

  retry(): void {
    this.reload.update((n) => n + 1);
  }
}
