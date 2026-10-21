import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import type { RadarApproveIn, RadarCandidate } from '../../api/models';

export const RELATIONSHIP_CHOICES: { value: RadarApproveIn['relationship']; label: string }[] = [
  { value: 'competitor', label: 'Competitor' },
  { value: 'coopetition', label: 'Coopetition (competes and partners)' },
  { value: 'partner', label: 'Partner' },
];

/** Track a radar candidate: becomes a tracked entity (approve). Returns the request body or nothing. */
@Component({
  selector: 'fr-track-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form (submit)="submit($event)" novalidate>
      <h2 mat-dialog-title class="title">Track {{ data.name }}</h2>
      <mat-dialog-content class="body">
        <p class="small muted">It becomes a tracked company or product: its signals are linked and it can get a battlecard.</p>
        <div class="fr-field">
          <label for="track-rel">Relationship to JFrog</label>
          <select id="track-rel" class="fr-select" (change)="relationship.set($any($event.target).value)">
            @for (r of choices; track r.value) {
              <option [value]="r.value" [selected]="relationship() === r.value">{{ r.label }}</option>
            }
          </select>
        </div>
        <div class="fr-field">
          <label for="track-name">Display name</label>
          <input id="track-name" class="fr-input" type="text" maxlength="100" [value]="name()" (input)="name.set($any($event.target).value)"
                 [attr.aria-invalid]="nameError() ? 'true' : null" [attr.aria-describedby]="nameError() ? 'track-name-error' : null" required />
          @if (nameError()) { <span id="track-name-error" class="error small">{{ nameError() }}</span> }
        </div>
        @if (data.repo_url) {
          <label class="fr-check">
            <input type="checkbox" [checked]="addSource()" (change)="addSource.set($any($event.target).checked)" />
            <span>Add its GitHub repo as an inactive source <span class="small muted">({{ repo() }})</span></span>
          </label>
        }
      </mat-dialog-content>
      <mat-dialog-actions class="actions">
        <button type="button" class="fr-button" (click)="ref.close()">Cancel</button>
        <button type="submit" class="fr-button fr-button--primary">Track</button>
      </mat-dialog-actions>
    </form>
  `,
  styleUrl: './dialog.scss',
})
export class TrackDialogComponent {
  protected readonly data = inject<RadarCandidate>(MAT_DIALOG_DATA);
  protected readonly ref = inject<MatDialogRef<TrackDialogComponent, RadarApproveIn>>(MatDialogRef);
  protected readonly choices = RELATIONSHIP_CHOICES;
  protected readonly relationship = signal<RadarApproveIn['relationship']>('competitor');
  protected readonly name = signal(this.data.name);
  protected readonly addSource = signal(true);
  private readonly touched = signal(false);
  protected readonly nameError = computed(() =>
    this.touched() && this.name().trim().length < 2 ? 'Enter a name of at least 2 characters.' : '',
  );
  protected readonly repo = computed(() => this.data.repo_url?.replace(/^https?:\/\/(www\.)?github\.com\//, '') ?? '');

  submit(event: Event): void {
    event.preventDefault();
    this.touched.set(true);
    if (this.nameError()) return;
    this.ref.close({
      relationship: this.relationship(),
      name: this.name().trim(),
      add_sources: !!this.data.repo_url && this.addSource(),
    });
  }
}
