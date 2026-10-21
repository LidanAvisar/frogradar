import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import type { ReviewQueue, Scorecard, Signal } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { type SignalCardAction, SignalCardComponent } from '../../shared/signal-card.component';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { ScorecardPanelComponent } from './scorecard-panel.component';
import { SignalActionsService } from './signal-actions.service';

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; queue: ReviewQueue };

const SHORTCUTS: Record<string, SignalCardAction | 'next' | 'prev'> = {
  a: 'approve',
  c: 'correct',
  r: 'reject',
  j: 'next',
  k: 'prev',
};

/** True if a key event belongs to text entry (never hijack typing). */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/**
 * Route /review: signals waiting for review (impact × confidence), one in focus. Keyboard:
 * A approve, C correct, R reject, J / K next / previous. The AI vs Analyst scorecard sits on top.
 */
@Component({
  selector: 'fr-review-page',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, ScorecardPanelComponent, SignalCardComponent, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './review-page.component.html',
  styleUrl: './review-page.component.scss',
  host: { '(document:keydown)': 'onKey($event)' },
})
export class ReviewPageComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly actions = inject(SignalActionsService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly scorecard = signal<Scorecard | null>(null);
  /** Local copies after actions (optimistic, server result, rollback), by id. */
  private readonly patches = signal(new Map<number, Signal>());
  protected readonly index = signal(0);

  protected readonly items = computed(() => {
    const s = this.state();
    if (s.status !== 'ready') return [];
    const p = this.patches();
    return s.queue.items.map((i) => p.get(i.id) ?? i);
  });
  protected readonly current = computed(() => this.items()[this.index()] ?? null);
  protected readonly progress = computed(() => {
    const s = this.state();
    if (s.status !== 'ready') return null;
    const done = this.items().filter((i) => i.review && i.review.status !== 'new').length;
    return { reviewed: s.queue.progress.reviewed + done, total: s.queue.progress.total };
  });
  protected readonly remaining = computed(() => this.items().filter((i) => !i.review).length);
  protected readonly legend = [
    { key: 'A', label: 'Approve' },
    { key: 'C', label: 'Correct' },
    { key: 'R', label: 'Reject' },
    { key: 'J', label: 'Next' },
    { key: 'K', label: 'Previous' },
  ];

  constructor() {
    this.actions.changed.pipe(takeUntilDestroyed()).subscribe(() => this.loadScorecard());
  }

  ngOnInit(): void {
    this.load();
    this.loadScorecard();
  }

  load(): void {
    this.state.set({ status: 'loading' });
    this.api
      .reviewQueue()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (queue) => {
          this.patches.set(new Map());
          this.index.set(0);
          this.state.set({ status: 'ready', queue });
        },
        error: () => this.state.set({ status: 'error' }),
      });
  }

  private loadScorecard(): void {
    this.api.scorecard().subscribe({ next: (s) => this.scorecard.set(s), error: () => undefined });
  }

  act(action: SignalCardAction, s: Signal | null = this.current()): void {
    if (!s || (s.review && s.review.status === 'rejected')) return;
    if (action === 'approve' && s.review) return;
    this.actions.act(action, s, (next) => {
      // null = rejected: it stays in the queue view, marked, without actions.
      const reviewed: Signal = next ?? {
        ...s,
        review: { status: 'rejected', by: null, at: null, edited_fields: s.review?.edited_fields ?? [] },
      };
      this.patches.update((m) => new Map(m).set(s.id, reviewed));
      // Moving on after an action; a rollback (no review) brings the signal back into focus.
      if (reviewed.review) {
        if (this.current()?.id === s.id) this.next(true);
      } else {
        this.focusOn(s.id);
      }
    });
  }

  /** Next signal; `pending` skips ones already reviewed. */
  next(pending = false): void {
    const list = this.items();
    for (let i = this.index() + 1; i < list.length; i++) {
      if (!pending || !list[i].review) return this.index.set(i);
    }
    if (!pending && this.index() < list.length - 1) this.index.set(list.length - 1);
  }

  prev(): void {
    this.index.update((i) => Math.max(0, i - 1));
  }

  private focusOn(id: number): void {
    const i = this.items().findIndex((s) => s.id === id);
    if (i >= 0) this.index.set(i);
  }

  protected onKey(e: KeyboardEvent): void {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (isTyping(e.target) || this.actions.dialogOpen()) return;
    const cmd = SHORTCUTS[e.key.toLowerCase()];
    if (!cmd || this.state().status !== 'ready') return;
    e.preventDefault();
    if (cmd === 'next') this.next();
    else if (cmd === 'prev') this.prev();
    else this.act(cmd);
  }

  protected select(i: number): void {
    this.index.set(i);
  }
}
