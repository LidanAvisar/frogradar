import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Router } from '@angular/router';
import type { Observable } from 'rxjs';

import type { RadarApproveIn, RadarCandidate, RadarCandidateDetail, RadarDismissIn } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { NavCountsService } from '../../core/nav-counts.service';
import { DismissDialogComponent } from './dismiss-dialog.component';
import { TrackDialogComponent } from './track-dialog.component';

export type CandidatePatch = Partial<RadarCandidateDetail>;
/** Applies a patch to the page's copy of the candidate (optimistic update, server result, rollback). */
export type Apply = (patch: CandidatePatch) => void;
/** Where focus goes when the control that had it disappears (e.g. the action buttons). */
export type FocusFallback = () => HTMLElement | null;

const DIALOG = { width: '480px', maxWidth: '100vw', panelClass: 'fr-dialog', autoFocus: 'first-tabbable' as const };

/**
 * Watch / Track / Dismiss for radar candidates. Optimistic: the status changes at once, the API
 * result replaces it, and an error restores the previous state. Results are announced in a
 * snackbar (a polite live region).
 */
@Injectable({ providedIn: 'root' })
export class RadarActionsService {
  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly nav = inject(NavCountsService);
  private readonly router = inject(Router);

  watch(c: RadarCandidate, apply: Apply, focus?: FocusFallback): void {
    this.run(c, { status: 'watching' }, this.api.watchCandidate(c.id), (r) => r, this.keepFocus(apply, focus), `Watching ${c.name}`, `Could not watch ${c.name}`);
  }

  track(c: RadarCandidate, apply: Apply, focus?: FocusFallback): void {
    this.dialog
      .open<TrackDialogComponent, RadarCandidate, RadarApproveIn>(TrackDialogComponent, { ...DIALOG, data: c })
      .afterClosed()
      .subscribe((body) => {
        if (!body) return;
        this.run(
          c,
          { status: 'approved' },
          this.api.approveCandidate(c.id, body),
          (r) => r.candidate,
          this.keepFocus(apply, focus),
          (r) => `Now tracking ${r.entity.name} as ${body.relationship}`,
          `Could not track ${c.name}`,
          (r) => (r.entity.type === 'company' ? ['/battlecards', r.entity.slug] : null),
        );
      });
  }

  dismiss(c: RadarCandidate, apply: Apply, focus?: FocusFallback): void {
    this.dialog
      .open<DismissDialogComponent, RadarCandidate, RadarDismissIn>(DismissDialogComponent, { ...DIALOG, data: c })
      .afterClosed()
      .subscribe((body) => {
        if (!body) return;
        this.run(
          c,
          { status: 'dismissed', dismiss_reason: body.reason, dismiss_comment: body.comment ?? null },
          this.api.dismissCandidate(c.id, body),
          (r) => r,
          this.keepFocus(apply, focus),
          `Dismissed ${c.name}`,
          `Could not dismiss ${c.name}`,
        );
      });
  }

  /** After each patch, if focus fell back to the page body, move it to the fallback element. */
  private keepFocus(apply: Apply, focus?: FocusFallback): Apply {
    return (patch) => {
      apply(patch);
      if (!focus) return;
      setTimeout(() => {
        const active = document.activeElement;
        if (!active || active === document.body) focus()?.focus();
      });
    };
  }

  private run<T>(
    c: RadarCandidate,
    optimistic: CandidatePatch,
    call: Observable<T>,
    result: (r: T) => CandidatePatch,
    apply: Apply,
    ok: string | ((r: T) => string),
    failed: string,
    link?: (r: T) => string[] | null,
  ): void {
    const before: CandidatePatch = { status: c.status };
    if ('dismiss_reason' in optimistic) {
      const d = c as Partial<RadarCandidateDetail>;
      before.dismiss_reason = d.dismiss_reason ?? null;
      before.dismiss_comment = d.dismiss_comment ?? null;
    }
    apply(optimistic);
    call.subscribe({
      next: (r) => {
        apply(result(r));
        this.nav.refresh();
        const target = link?.(r) ?? null;
        const ref = this.snack.open(typeof ok === 'string' ? ok : ok(r), target ? 'Open battlecard' : 'OK', {
          duration: 6000,
          politeness: 'polite',
        });
        if (target) ref.onAction().subscribe(() => this.router.navigate(target));
      },
      error: () => {
        apply(before);
        this.snack.open(`${failed}. Nothing was changed.`, 'OK', { duration: 8000, politeness: 'assertive' });
      },
    });
  }
}
