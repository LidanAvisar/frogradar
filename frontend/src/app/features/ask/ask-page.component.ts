import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ActivatedRoute, Router } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs';

import type { AskAnswer, AskFeedbackIn } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { AskRecentService } from '../../core/ask-recent.service';
import { plural } from '../../core/format';
import { ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { AnswerCardComponent, type Feedback } from './answer-card.component';
import { AskFeedbackDialogComponent } from './feedback-dialog.component';
import { SourcesRailComponent } from './sources-rail.component';

const STARTERS = [
  'What changed in Sonatype’s pricing this year?',
  'Who launched a package firewall?',
  'What supply-chain security incidents happened recently?',
];

/**
 * Ask your intelligence (docs/design Ask): a conversation thread of checked, cited answers, the
 * Sources rail for the selected answer, follow-up chips and the question input. The conversation id
 * is in the URL (?c=), so a conversation can be reopened from "Recent questions".
 */
@Component({
  selector: 'fr-ask-page',
  standalone: true,
  imports: [AnswerCardComponent, ErrorStateComponent, SkeletonComponent, SourcesRailComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './ask-page.component.html',
  styleUrl: './ask-page.component.scss',
})
export class AskPageComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly recent = inject(AskRecentService);
  private readonly input = viewChild<ElementRef<HTMLInputElement>>('question');

  protected readonly conversationId = signal<number | null>(null);
  protected readonly turns = signal<AskAnswer[]>([]);
  protected readonly pending = signal<string | null>(null);
  protected readonly loadingConversation = signal(false);
  protected readonly loadError = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly announcement = signal('');
  protected readonly selectedTurn = signal<number | null>(null);
  protected readonly feedback = signal<Partial<Record<number, Feedback>>>({});
  protected readonly draft = signal('');
  protected readonly starters = STARTERS;

  protected readonly selected = computed(() => {
    const list = this.turns();
    const id = this.selectedTurn();
    return list.find((t) => t.turn_id === id) ?? list[list.length - 1] ?? null;
  });
  protected readonly followups = computed(() => {
    const list = this.turns();
    return this.pending() ? [] : (list[list.length - 1]?.suggested_followups ?? []);
  });

  constructor() {
    this.recent.refresh();
    this.route.queryParamMap
      .pipe(
        map((p) => Number(p.get('c')) || null),
        distinctUntilChanged(),
        takeUntilDestroyed(),
      )
      .subscribe((id) => {
        if (id === this.conversationId() && id !== null) return;
        this.conversationId.set(id);
        this.turns.set([]);
        this.selectedTurn.set(null);
        this.error.set(null);
        if (id !== null) this.load(id);
      });
  }

  load(id: number): void {
    this.loadingConversation.set(true);
    this.loadError.set(false);
    this.api.askConversation(id).subscribe({
      next: (c) => {
        this.turns.set(c.turns);
        this.loadingConversation.set(false);
      },
      error: () => {
        this.loadingConversation.set(false);
        this.loadError.set(true);
      },
    });
  }

  submit(event: Event | null, text?: string): void {
    event?.preventDefault();
    const question = (text ?? this.draft()).trim();
    if (question.length < 3 || this.pending()) {
      if (question.length < 3) this.error.set('Ask a question of at least 3 characters.');
      return;
    }
    this.error.set(null);
    this.pending.set(question);
    this.announcement.set('Searching your intelligence…');
    this.api.ask({ question, conversation_id: this.conversationId() }).subscribe({
      next: (answer) => {
        this.pending.set(null);
        this.draft.set('');
        this.turns.update((t) => [...t, answer]);
        this.selectedTurn.set(answer.turn_id);
        this.announcement.set(
          answer.not_enough_evidence
            ? 'Answer ready: not enough evidence in FrogRadar.'
            : `Answer ready: ${plural(answer.points.length, 'point')} from ${plural(answer.sources.length, 'document')}.`,
        );
        if (this.conversationId() !== answer.conversation_id) {
          this.conversationId.set(answer.conversation_id);
          this.router.navigate([], { relativeTo: this.route, queryParams: { c: answer.conversation_id } });
        }
        this.recent.refresh();
        this.input()?.nativeElement.focus();
      },
      error: (e: HttpErrorResponse) => {
        this.pending.set(null);
        const detail = typeof e.error?.detail === 'string' ? e.error.detail : null;
        this.error.set(detail ?? 'The answer could not be created. Check that the API is running and try again.');
        this.announcement.set('The question could not be answered.');
      },
    });
  }

  newConversation(): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: {} });
    this.conversationId.set(null);
    this.turns.set([]);
    this.draft.set('');
    this.input()?.nativeElement.focus();
  }

  /** Citation chip: show that answer's sources and move focus to the cited entry. */
  showSource(turnId: number, n: number): void {
    this.selectedTurn.set(turnId);
    setTimeout(() => {
      const target = document.getElementById(`src-${turnId}-${n}`);
      target?.focus();
      target?.scrollIntoView?.({ block: 'nearest' });
    });
  }

  rate(turn: AskAnswer, kind: Feedback): void {
    if (kind === 'helpful') {
      this.send(turn, { helpful: true }, 'Thanks: marked as helpful.');
      return;
    }
    this.dialog
      .open<AskFeedbackDialogComponent, unknown, AskFeedbackIn>(AskFeedbackDialogComponent, {
        width: '480px',
        maxWidth: '100vw',
        panelClass: 'fr-dialog',
        autoFocus: 'first-tabbable',
      })
      .afterClosed()
      .subscribe((body) => body && this.send(turn, body, 'Thanks: your report was saved.'));
  }

  private send(turn: AskAnswer, body: AskFeedbackIn, ok: string): void {
    const before = this.feedback()[turn.turn_id];
    this.feedback.update((f) => ({ ...f, [turn.turn_id]: body.helpful ? 'helpful' : 'wrong' }));
    this.api.askFeedback(turn.turn_id, body).subscribe({
      next: () => this.snack.open(ok, 'OK', { duration: 5000, politeness: 'polite' }),
      error: () => {
        this.feedback.update((f) => {
          const next = { ...f };
          if (before) next[turn.turn_id] = before;
          else delete next[turn.turn_id];
          return next;
        });
        this.snack.open('Feedback could not be saved.', 'OK', { duration: 8000, politeness: 'assertive' });
      },
    });
  }
}
