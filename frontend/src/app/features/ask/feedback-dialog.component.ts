import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import type { AskFeedbackIn } from '../../api/models';

export const ASK_REASONS: { value: NonNullable<AskFeedbackIn['reason']>; label: string }[] = [
  { value: 'unsupported_claim', label: 'A point is not supported by its source' },
  { value: 'inaccurate_summary', label: 'A fact is wrong' },
  { value: 'missing_information', label: 'Something important is missing' },
  { value: 'outdated', label: 'It is out of date' },
  { value: 'not_relevant', label: 'It does not answer the question' },
  { value: 'other', label: 'Other' },
];

/** "Something's wrong" with an answer: a reason and an optional comment. */
@Component({
  selector: 'fr-ask-feedback-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form (submit)="submit($event)" novalidate>
      <h2 mat-dialog-title class="title">What's wrong with this answer?</h2>
      <mat-dialog-content class="body">
        <fieldset class="reasons" [attr.aria-describedby]="error() ? 'ask-reason-error' : null">
          <legend class="fr-field-label">Reason</legend>
          @for (r of reasons; track r.value) {
            <label class="fr-check">
              <input type="radio" name="ask-reason" [value]="r.value" [checked]="reason() === r.value" (change)="reason.set(r.value)" />
              <span>{{ r.label }}</span>
            </label>
          }
          @if (error()) { <span id="ask-reason-error" class="error small">{{ error() }}</span> }
        </fieldset>
        <div class="fr-field">
          <label for="ask-comment">Comment <span class="muted">(optional)</span></label>
          <textarea id="ask-comment" class="fr-textarea" maxlength="500" (input)="comment.set($any($event.target).value)"></textarea>
        </div>
      </mat-dialog-content>
      <mat-dialog-actions class="actions">
        <button type="button" class="fr-button" (click)="ref.close()">Cancel</button>
        <button type="submit" class="fr-button fr-button--primary">Send</button>
      </mat-dialog-actions>
    </form>
  `,
  styleUrl: '../radar/dialog.scss',
})
export class AskFeedbackDialogComponent {
  protected readonly ref = inject<MatDialogRef<AskFeedbackDialogComponent, AskFeedbackIn>>(MatDialogRef);
  protected readonly reasons = ASK_REASONS;
  protected readonly reason = signal<string>('');
  protected readonly comment = signal('');
  private readonly touched = signal(false);
  protected readonly error = computed(() => (this.touched() && !this.reason() ? 'Choose a reason.' : ''));

  submit(event: Event): void {
    event.preventDefault();
    this.touched.set(true);
    if (this.error()) return;
    this.ref.close({
      helpful: false,
      reason: this.reason() as AskFeedbackIn['reason'],
      comment: this.comment().trim() || null,
    });
  }
}
