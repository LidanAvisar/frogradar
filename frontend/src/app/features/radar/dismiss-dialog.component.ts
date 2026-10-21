import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import type { RadarCandidate, RadarDismissIn } from '../../api/models';
import { DISMISS_REASONS } from '../../core/format';

/** Dismiss a radar candidate with a reason code and an optional comment. */
@Component({
  selector: 'fr-dismiss-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form (submit)="submit($event)" novalidate>
      <h2 mat-dialog-title class="title">Dismiss {{ data.name }}</h2>
      <mat-dialog-content class="body">
        <fieldset class="reasons" [attr.aria-describedby]="reasonError() ? 'dismiss-reason-error' : null">
          <legend class="fr-field-label">Reason</legend>
          @for (r of reasons; track r.value) {
            <label class="fr-check reason">
              <input type="radio" name="reason" [value]="r.value" [checked]="reason() === r.value" (change)="reason.set(r.value)" />
              <span><span class="label">{{ r.label }}</span> <span class="small muted">{{ r.hint }}</span></span>
            </label>
          }
          @if (reasonError()) { <span id="dismiss-reason-error" class="error small">{{ reasonError() }}</span> }
        </fieldset>
        <div class="fr-field">
          <label for="dismiss-comment">Comment <span class="muted">(optional)</span></label>
          <textarea id="dismiss-comment" class="fr-textarea" maxlength="500" (input)="comment.set($any($event.target).value)"></textarea>
          <span class="small muted">{{ comment().length }} / 500</span>
        </div>
      </mat-dialog-content>
      <mat-dialog-actions class="actions">
        <button type="button" class="fr-button" (click)="ref.close()">Cancel</button>
        <button type="submit" class="fr-button fr-button--primary">Dismiss</button>
      </mat-dialog-actions>
    </form>
  `,
  styleUrl: './dialog.scss',
})
export class DismissDialogComponent {
  protected readonly data = inject<RadarCandidate>(MAT_DIALOG_DATA);
  protected readonly ref = inject<MatDialogRef<DismissDialogComponent, RadarDismissIn>>(MatDialogRef);
  protected readonly reasons = DISMISS_REASONS;
  protected readonly reason = signal<string>('');
  protected readonly comment = signal('');
  private readonly touched = signal(false);
  protected readonly reasonError = computed(() => (this.touched() && !this.reason() ? 'Choose a reason.' : ''));

  submit(event: Event): void {
    event.preventDefault();
    this.touched.set(true);
    if (this.reasonError()) return;
    this.ref.close({
      reason: this.reason() as RadarDismissIn['reason'],
      comment: this.comment().trim() || null,
    });
  }
}
