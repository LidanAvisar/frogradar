import { LiveAnnouncer } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';

import type { Brief } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { BriefViewService } from '../../core/brief-view.service';
import { eyebrowDate } from '../../core/format';
import { PreferencesService } from '../../core/preferences.service';
import { IconComponent } from '../../shared/icon.component';
import { SignalCardComponent } from '../../shared/signal-card.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { PipelineStatusComponent } from './pipeline-status.component';
import { PricingWatchComponent } from './pricing-watch.component';
import { SinceYesterdayComponent } from './since-yesterday.component';
import { TopStoryComponent } from './top-story.component';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; brief: Brief };


/** Route "/": stat tiles, the top story, other developments, since yesterday, pricing watch, pipeline. */
@Component({
  selector: 'fr-brief-page',
  standalone: true,
  imports: [
    EmptyStateComponent,
    RouterLink,
    TopStoryComponent,
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
  protected readonly story = computed(() => this.brief()?.top_story ?? null);
  protected readonly developments = computed(() => this.brief()?.developments ?? []);
  protected readonly devIds = computed(() => this.developments().map((s) => s.id));
  protected readonly allExpanded = computed(() => this.view.allExpanded(this.devIds()));
  /** Stat tiles: each opens the Signals list with the matching filter. */
  protected readonly tiles = computed(() => {
    const st = this.brief()?.stats;
    if (!st) return [];
    return [
      { n: st.new_signals, label: st.new_signals === 1 ? 'new signal' : 'new signals',
        hint: 'since yesterday', params: { since: '24h' } },
      { n: st.high_impact, label: 'high impact', hint: 'new, impact 4-5', params: { since: '24h', min_impact: 4 } },
      { n: st.price_changes, label: st.price_changes === 1 ? 'price change' : 'price changes',
        hint: `last ${st.price_change_days} days`, params: { kind: 'page_diff', days: st.price_change_days } },
    ];
  });
  protected readonly skeletons = [1, 2, 3];

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
    this.view.setAll(this.devIds(), open);
    this.announcer.announce(open ? 'All developments expanded' : 'All developments collapsed');
  }

  toggleExecutive(): void {
    const on = !this.executive();
    this.prefs.setExecutiveView(on);
    this.announcer.announce(on ? 'Executive view on: stats and top story only' : 'Executive view off');
  }

  search(event: Event, query: string): void {
    event.preventDefault();
    const q = query.trim();
    this.router.navigate(['/signals'], q ? { queryParams: { q } } : {});
  }
}
