import { LiveAnnouncer } from '@angular/cdk/a11y';
import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';

import type { Brief, CitationRef } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { BriefViewService } from '../../core/brief-view.service';
import { eyebrowDate } from '../../core/format';
import { PreferencesService } from '../../core/preferences.service';
import { CompanyChipComponent } from '../../shared/company-chip.component';
import { IconComponent } from '../../shared/icon.component';
import { SignalCardComponent } from '../../shared/signal-card.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { PipelineStatusComponent } from './pipeline-status.component';
import { PricingWatchComponent } from './pricing-watch.component';
import { SinceYesterdayComponent } from './since-yesterday.component';

interface Cite {
  n: number;
  ref: CitationRef;
}

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; brief: Brief };

const DEFAULT_HEADLINE = 'Your morning brief';
const MAX_TODAY = 3;
/** Longer sentences are clamped to two lines (full text stays in the DOM and the title). */
const CLAMP_AFTER_CHARS = 110;

/** Route "/": top developments, what changed since yesterday, pricing watch, pipeline status. */
@Component({
  selector: 'fr-brief-page',
  standalone: true,
  imports: [
    CompanyChipComponent,
    EmptyStateComponent,
    NgTemplateOutlet,
    ErrorStateComponent,
    IconComponent,
    PipelineStatusComponent,
    PricingWatchComponent,
    SignalCardComponent,
    SinceYesterdayComponent,
    SkeletonComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './brief-page.component.html',
  styleUrl: './brief-page.component.scss',
})
export class BriefPageComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly announcer = inject(LiveAnnouncer);
  private readonly destroyRef = inject(DestroyRef);
  private readonly prefs = inject(PreferencesService);

  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly view = inject(BriefViewService);
  protected readonly executive = this.prefs.executiveView;
  protected readonly brief = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.brief : null;
  });
  protected readonly dateLine = computed(() => eyebrowDate(this.brief()?.generated_at ?? new Date().toISOString()));
  protected readonly headline = computed(() => this.brief()?.headline ?? DEFAULT_HEADLINE);
  /** Citation numbers, in order of first use across the headline and the overview sentences. */
  private readonly citeNumbers = computed(() => {
    const b = this.brief();
    const order = new Map<number, number>();
    const refs = [...(b?.headline_citations ?? []), ...(b?.overview_sentences ?? []).flatMap((s) => s.citations)];
    for (const r of refs) if (!order.has(r.id)) order.set(r.id, order.size + 1);
    return order;
  });
  protected readonly headlineCites = computed(() => this.cites(this.brief()?.headline_citations ?? []));
  /** "What matters today": up to 3 overview sentences, each with its company, products and sources. */
  protected readonly today = computed(() =>
    (this.brief()?.overview_sentences ?? []).slice(0, MAX_TODAY).map((s) => ({
      text: s.text,
      long: s.text.length > CLAMP_AFTER_CHARS,
      company: s.company ?? null,
      affects: s.affects ?? [],
      cites: this.cites(s.citations),
    })),
  );
  protected readonly newCount = computed(() => this.brief()?.since_yesterday_total ?? 0);
  protected readonly highImpactNew = computed(() => this.brief()?.since_yesterday_high_impact ?? 0);
  protected readonly topIds = computed(() => this.brief()?.top.map((s) => s.id) ?? []);
  protected readonly allExpanded = computed(() => this.view.allExpanded(this.topIds()));
  protected readonly skeletons = [1, 2, 3];

  private cites(refs: CitationRef[]): Cite[] {
    const numbers = this.citeNumbers();
    return refs.map((ref) => ({ n: numbers.get(ref.id) ?? 0, ref }));
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.state.set({ status: 'loading' });
    this.api
      .brief()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (brief) => this.state.set({ status: 'ready', brief }),
        error: () => this.state.set({ status: 'error' }),
      });
  }

  toggleAll(): void {
    const open = !this.allExpanded();
    this.view.setAll(this.topIds(), open);
    this.announcer.announce(open ? 'All developments expanded' : 'All developments collapsed');
  }

  toggleExecutive(): void {
    const on = !this.executive();
    this.prefs.setExecutiveView(on);
    this.announcer.announce(on ? 'Executive view on: one line per development' : 'Executive view off');
  }

  search(event: Event, query: string): void {
    event.preventDefault();
    const q = query.trim();
    this.router.navigate(['/signals'], q ? { queryParams: { q } } : {});
  }
}
