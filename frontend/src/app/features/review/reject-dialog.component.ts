import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import type { Signal, SignalRejectIn } from '../../api/models';
import { SIGNAL_REASONS } from '../../core/format';

export type RejectBody = Omit<SignalRejectIn, 'analyst'>;

const REJECT_REASONS = SIGNAL_REASONS.filter((r) =>
  ['not_relevant', 'duplicate', 'outdated', 'inaccurate_summary', 'unsupported_claim', 'other'].includes(r.value),
);

/** Reject a signal (not relevant or wrong). "Not relevant" is preselected, so Enter confirms. */
@Component({
  selector: 'fr-reject-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form (submit)="submit($event)" novalidate>
      <h2 mat-dialog-title class="title">Reject this signal</h2>
      <mat-dialog-content class="body">
        <p class="small muted signal-title">{{ data.title }}</p>
        <fieldset class="reasons">
          <legend class="fr-field-label">Reason</legend>
          @for (r of reasons; track r.value) {
            <label class="fr-check reason">
              <input type="radio" name="reason" [value]="r.value" [checked]="reason() === r.value" (change)="reason.set(r.value)" />
              <span class="label">{{ r.label }}</span>
            </label>
          }
        </fieldset>
        <div class="fr-field">
          <label for="reject-comment">Comment <span class="muted">(optional)</span></label>
          <textarea id="reject-comment" class="fr-textarea" maxlength="500" (input)="comment.set($any($event.target).value)"></textarea>
        </div>
        <p class="small muted">It leaves the Brief and the lists; it stays in the database.</p>
      </mat-dialog-content>
      <mat-dialog-actions class="actions">
        <button type="button" class="fr-button" (click)="ref.close()">Cancel</button>
        <button type="submit" class="fr-button fr-button--primary">Reject</button>
      </mat-dialog-actions>
    </form>
  `,
  styleUrls: ['../radar/dialog.scss', './review-dialog.scss'],
})
export class RejectDialogComponent {
  protected readonly data = inject<Signal>(MAT_DIALOG_DATA);
  protected readonly ref = inject<MatDialogRef<RejectDialogComponent, RejectBody>>(MatDialogRef);
  protected readonly reasons = REJECT_REASONS;
  protected readonly reason = signal('not_relevant');
  protected readonly comment = signal('');

  submit(event: Event): void {
    event.preventDefault();
    this.ref.close({
      reason_code: this.reason() as RejectBody['reason_code'],
      comment: this.comment().trim() || null,
    });
  }
}
