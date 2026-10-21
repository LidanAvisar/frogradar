import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import type { Signal, SignalCorrectIn } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { CATEGORY_LABELS, SIGNAL_REASONS } from '../../core/format';

export type CorrectBody = Omit<SignalCorrectIn, 'analyst'>;

const NO_ENTITY = 'none';

/** The shown primary company (companies before products), or 'none'. */
export function primarySlug(s: Signal): string {
  const primary = s.entities.filter((e) => e.primary);
  return (primary.find((e) => e.type === 'company') ?? primary[0])?.slug ?? NO_ENTITY;
}

/**
 * Correct a signal: category, impact, relevance, primary company, a reason code (required) and an
 * optional comment. Fields start at the shown values; only changed fields are sent.
 */
@Component({
  selector: 'fr-correct-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form (submit)="submit($event)" novalidate>
      <h2 mat-dialog-title class="title">Correct this signal</h2>
      <mat-dialog-content class="body">
        <p class="small muted signal-title">{{ data.title }}</p>
        <div class="grid">
          <div class="fr-field">
            <label for="correct-category">Category</label>
            <select id="correct-category" class="fr-select" (change)="category.set($any($event.target).value)">
              @for (c of categories; track c.value) {
                <option [value]="c.value" [selected]="c.value === category()">{{ c.label }}</option>
              }
            </select>
          </div>
          <div class="fr-field">
            <label for="correct-entity">Primary company</label>
            <select id="correct-entity" class="fr-select" (change)="entity.set($any($event.target).value)">
              <option value="none" [selected]="entity() === 'none'">None (industry news)</option>
              @for (e of entityOptions(); track e.slug) {
                <option [value]="e.slug" [selected]="e.slug === entity()">{{ e.name }}</option>
              }
            </select>
          </div>
        </div>
        <fieldset class="choices">
          <legend class="fr-field-label">Impact on JFrog</legend>
          <div class="row">
            @for (n of impacts; track n) {
              <label class="fr-chip-choice">
                <input type="radio" name="impact" [value]="n" [checked]="impact() === n" (change)="impact.set(n)" />
                <span>{{ n }}</span>
              </label>
            }
          </div>
        </fieldset>
        <fieldset class="choices">
          <legend class="fr-field-label">Relevant to JFrog</legend>
          <div class="row">
            <label class="fr-chip-choice">
              <input type="radio" name="relevant" [checked]="relevant()" (change)="relevant.set(true)" /><span>Yes</span>
            </label>
            <label class="fr-chip-choice">
              <input type="radio" name="relevant" [checked]="!relevant()" (change)="relevant.set(false)" /><span>No</span>
            </label>
          </div>
          @if (!relevant()) { <span class="small muted">The signal leaves the Brief and the lists.</span> }
        </fieldset>
        <div class="fr-field">
          <label for="correct-reason">Reason</label>
          <select id="correct-reason" class="fr-select" [attr.aria-describedby]="reasonError() ? 'correct-reason-error' : null"
                  [attr.aria-invalid]="reasonError() ? true : null" (change)="chooseReason($any($event.target).value)">
            <option value="" [selected]="!reason()">Choose a reason</option>
            @for (r of reasons; track r.value) {
              <option [value]="r.value" [selected]="r.value === reason()">{{ r.label }}</option>
            }
          </select>
          @if (reasonError()) { <span id="correct-reason-error" class="error small">{{ reasonError() }}</span> }
        </div>
        <div class="fr-field">
          <label for="correct-comment">Comment <span class="muted">(optional, teaches the pipeline)</span></label>
          <textarea id="correct-comment" class="fr-textarea" maxlength="500" (input)="comment.set($any($event.target).value)"></textarea>
          <span class="small muted">{{ comment().length }} / 500</span>
        </div>
        @if (changeError()) { <p class="error small" role="alert">{{ changeError() }}</p> }
      </mat-dialog-content>
      <mat-dialog-actions class="actions">
        <button type="button" class="fr-button" (click)="ref.close()">Cancel</button>
        <button type="submit" class="fr-button fr-button--primary">Save correction</button>
      </mat-dialog-actions>
    </form>
  `,
  styleUrls: ['../radar/dialog.scss', './review-dialog.scss'],
})
export class CorrectDialogComponent {
  protected readonly data = inject<Signal>(MAT_DIALOG_DATA);
  protected readonly ref = inject<MatDialogRef<CorrectDialogComponent, CorrectBody>>(MatDialogRef);
  protected readonly categories = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label }));
  protected readonly reasons = SIGNAL_REASONS;
  protected readonly impacts = [1, 2, 3, 4, 5];

  private readonly start = {
    category: this.data.category ?? 'other',
    impact: this.data.impact ?? 3,
    relevant: true,
    primary_entity: primarySlug(this.data),
  };
  protected readonly category = signal<string>(this.start.category);
  protected readonly impact = signal<number>(this.start.impact);
  protected readonly relevant = signal<boolean>(true);
  protected readonly entity = signal<string>(this.start.primary_entity);
  protected readonly comment = signal('');
  private readonly chosenReason = signal<string>('');
  private readonly touched = signal(false);
  private readonly allEntities = signal<{ slug: string; name: string }[]>([]);

  protected readonly entityOptions = computed(() => {
    const seen = new Set<string>();
    return [...this.data.entities, ...this.allEntities()].filter((e) => !seen.has(e.slug) && seen.add(e.slug));
  });

  /** Only the fields that differ from the shown values. */
  protected readonly changes = computed(() => {
    const out: Partial<CorrectBody> = {};
    if (this.category() !== this.start.category) out.category = this.category() as CorrectBody['category'];
    if (this.impact() !== this.start.impact) out.impact = this.impact();
    if (!this.relevant()) out.relevant = false;
    if (this.entity() !== this.start.primary_entity) out.primary_entity = this.entity();
    return out;
  });

  /** A reason the analyst picked, else one implied by the change (impact down → too high). */
  protected readonly reason = computed(() => {
    if (this.chosenReason()) return this.chosenReason();
    const c = this.changes();
    if (c.relevant === false) return 'not_relevant';
    if (c.impact != null) return c.impact < this.start.impact ? 'impact_too_high' : 'impact_too_low';
    if (c.category) return 'wrong_category';
    if (c.primary_entity) return 'wrong_entity';
    return '';
  });
  protected readonly reasonError = computed(() => (this.touched() && !this.reason() ? 'Choose a reason.' : ''));
  protected readonly changeError = computed(() =>
    this.touched() && !Object.keys(this.changes()).length ? 'Change at least one field.' : '',
  );

  constructor() {
    inject(ApiService)
      .entities()
      .subscribe({ next: (list) => this.allEntities.set(list.filter((e) => !e.is_self)), error: () => undefined });
  }

  protected chooseReason(value: string): void {
    this.chosenReason.set(value);
  }

  submit(event: Event): void {
    event.preventDefault();
    this.touched.set(true);
    if (this.reasonError() || this.changeError()) return;
    this.ref.close({
      ...this.changes(),
      reason_code: this.reason() as CorrectBody['reason_code'],
      comment: this.comment().trim() || null,
    });
  }
}
