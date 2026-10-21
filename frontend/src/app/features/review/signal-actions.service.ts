import { Injectable, inject, signal } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subject, type Observable } from 'rxjs';

import type { Signal, SignalReview } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { NavCountsService } from '../../core/nav-counts.service';
import { PreferencesService } from '../../core/preferences.service';
import { CorrectDialogComponent, type CorrectBody } from './correct-dialog.component';
import { RejectDialogComponent, type RejectBody } from './reject-dialog.component';

export type SignalAction = 'approve' | 'correct' | 'reject';
/** Puts the page's copy of the signal in a state: a signal, or null = gone from this list. */
export type ApplySignal = (next: Signal | null) => void;
export type FocusFallback = () => HTMLElement | null;

const DIALOG = { width: '520px', maxWidth: '100vw', panelClass: 'fr-dialog', autoFocus: 'first-tabbable' as const };

/**
 * Approve / Correct / Reject a signal (Brief cards, Signals list, review queue). Optimistic: the
 * card changes at once, the API result replaces it, an error restores it. Results are announced
 * in a snackbar (polite; errors assertive), as on the Radar.
 */
@Injectable({ providedIn: 'root' })
export class SignalActionsService {
  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly nav = inject(NavCountsService);
  private readonly prefs = inject(PreferencesService);

  /** Emits after every action the server accepted (e.g. to reload the scorecard). */
  readonly changed = new Subject<Signal>();
  /** True while a Correct / Reject dialog is open (the review page pauses its shortcuts). */
  readonly dialogOpen = signal(false);

  act(action: SignalAction, s: Signal, apply: ApplySignal, focus?: FocusFallback): void {
    if (action === 'approve') this.approve(s, apply, focus);
    else if (action === 'correct') this.correct(s, apply, focus);
    else this.reject(s, apply, focus);
  }

  approve(s: Signal, apply: ApplySignal, focus?: FocusFallback): void {
    const analyst = this.prefs.analystName();
    this.run(
      s,
      { ...s, review: this.review('approved', []) },
      this.api.approveSignal(s.id, { analyst }),
      this.keepFocus(apply, focus),
      `Approved: ${s.title}`,
      'Could not approve the signal',
    );
  }

  correct(s: Signal, apply: ApplySignal, focus?: FocusFallback): void {
    this.dialogOpen.set(true);
    this.dialog
      .open<CorrectDialogComponent, Signal, CorrectBody>(CorrectDialogComponent, { ...DIALOG, data: s })
      .afterClosed()
      .subscribe((body) => {
        this.dialogOpen.set(false);
        if (!body) return;
        const fields = (['category', 'impact', 'relevant', 'primary_entity'] as const).filter((f) => body[f] != null);
        const optimistic: Signal | null =
          body.relevant === false
            ? null
            : {
                ...s,
                category: body.category ?? s.category,
                impact: body.impact ?? s.impact,
                entities: body.primary_entity
                  ? s.entities.map((e) => ({ ...e, primary: e.slug === body.primary_entity }))
                  : s.entities,
                review: this.review('approved', [...new Set([...(s.review?.edited_fields ?? []), ...fields])]),
              };
        this.run(
          s,
          optimistic,
          this.api.correctSignal(s.id, { ...body, analyst: this.prefs.analystName() }),
          this.keepFocus(apply, focus),
          body.relevant === false ? `Marked not relevant: ${s.title}` : `Corrected: ${s.title}`,
          'Could not save the correction',
        );
      });
  }

  reject(s: Signal, apply: ApplySignal, focus?: FocusFallback): void {
    this.dialogOpen.set(true);
    this.dialog
      .open<RejectDialogComponent, Signal, RejectBody>(RejectDialogComponent, { ...DIALOG, data: s })
      .afterClosed()
      .subscribe((body) => {
        this.dialogOpen.set(false);
        if (!body) return;
        this.run(
          s,
          null,
          this.api.rejectSignal(s.id, { ...body, analyst: this.prefs.analystName() }),
          this.keepFocus(apply, focus),
          `Rejected: ${s.title}. It no longer appears in the Brief or lists.`,
          'Could not reject the signal',
        );
      });
  }

  private review(status: SignalReview['status'], edited: string[]): SignalReview {
    return { status, by: this.prefs.analystName(), at: new Date().toISOString(), edited_fields: edited };
  }

  private keepFocus(apply: ApplySignal, focus?: FocusFallback): ApplySignal {
    return (next) => {
      apply(next);
      if (!focus) return;
      setTimeout(() => {
        const active = document.activeElement;
        if (!active || active === document.body) focus()?.focus();
      });
    };
  }

  private run(
    before: Signal,
    optimistic: Signal | null,
    call: Observable<Signal>,
    apply: ApplySignal,
    ok: string,
    failed: string,
  ): void {
    apply(optimistic);
    call.subscribe({
      next: (r) => {
        apply(r.review?.status === 'rejected' ? null : r);
        this.nav.refresh();
        this.changed.next(r);
        this.snack.open(ok, 'OK', { duration: 6000, politeness: 'polite' });
      },
      error: () => {
        apply(before);
        this.snack.open(`${failed}. Nothing was changed.`, 'OK', { duration: 8000, politeness: 'assertive' });
      },
    });
  }
}

/**
 * Local changes on top of a list from the server: a replaced signal, or null = removed.
 * Pages render `view(list)`; a rollback simply sets the original back.
 */
export class SignalOverlay {
  private readonly patches = signal(new Map<number, Signal | null>());

  view<T extends Signal>(list: readonly T[]): T[] {
    const p = this.patches();
    if (!p.size) return [...list];
    return list.flatMap((s) => {
      if (!p.has(s.id)) return [s];
      const next = p.get(s.id);
      return next ? [{ ...s, ...next } as T] : [];
    });
  }

  applier(id: number): ApplySignal {
    return (next) => this.patches.update((m) => new Map(m).set(id, next));
  }

  reset(): void {
    this.patches.set(new Map());
  }
}
