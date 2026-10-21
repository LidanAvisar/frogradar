import { LiveAnnouncer } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';

import type { Brief } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { eyebrowDate } from '../../core/format';
import { PreferencesService } from '../../core/preferences.service';
import { IconComponent } from '../../shared/icon.component';
import { SignalCardComponent } from '../../shared/signal-card.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { PipelineStatusComponent } from './pipeline-status.component';
import { PricingWatchComponent } from './pricing-watch.component';
import { SinceYesterdayComponent } from './since-yesterday.component';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; brief: Brief };

const DEFAULT_HEADLINE = 'Your morning brief';

/** Route "/": top developments, what changed since yesterday, pricing watch, pipeline status. */
@Component({
  selector: 'fr-brief-page',
  standalone: true,
  imports: [
    EmptyStateComponent,
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
  protected readonly executive = this.prefs.executiveView;
  protected readonly brief = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.brief : null;
  });
  protected readonly dateLine = computed(() => eyebrowDate(this.brief()?.generated_at ?? new Date().toISOString()));
  protected readonly headline = computed(() => this.brief()?.headline ?? DEFAULT_HEADLINE);
  protected readonly newCount = computed(() => this.brief()?.since_yesterday.length ?? 0);
  protected readonly highImpactNew = computed(
    () => this.brief()?.since_yesterday.filter((i) => (i.impact ?? 0) >= 4).length ?? 0,
  );
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
