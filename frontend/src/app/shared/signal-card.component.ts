import { ChangeDetectionStrategy, Component, computed, inject, input, model, output } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { Signal } from '../api/models';
import { categoryLabel, editedFieldsLabel, firstSentence, shortDate, verificationLabel } from '../core/format';
import { HighlightPipe } from '../core/highlight.pipe';
import { JfrogTermsService } from '../core/jfrog-terms.service';
import { CompanyChipComponent } from './company-chip.component';
import { ConfidenceBadgeComponent } from './confidence-badge.component';
import { EvidenceQuoteComponent } from './evidence-quote.component';
import { IconComponent } from './icon.component';
import { ImpactBadgeComponent } from './impact-badge.component';
import { SourceLinkComponent } from './source-link.component';
import { VendorClaimTagComponent } from './vendor-claim-tag.component';

export type SignalCardVariant = 'analyst' | 'executive';
export type SignalCardAction = 'approve' | 'correct' | 'reject';

/**
 * One signal.
 * analyst: collapsible card. Collapsed = header row + takeaway; expanded adds summary, full
 *   "why", evidence, source line and actions. `expanded` is two-way ([(expanded)]).
 * executive: rank, title (link to the source) and takeaway only.
 */
@Component({
  selector: 'fr-signal-card',
  standalone: true,
  imports: [
    NgTemplateOutlet,
    RouterLink,
    CompanyChipComponent,
    ConfidenceBadgeComponent,
    EvidenceQuoteComponent,
    HighlightPipe,
    IconComponent,
    ImpactBadgeComponent,
    SourceLinkComponent,
    VendorClaimTagComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './signal-card.component.html',
  styleUrl: './signal-card.component.scss',
})
export class SignalCardComponent {
  readonly signal = input.required<Signal>();
  readonly rank = input<number | null>(null);
  readonly variant = input<SignalCardVariant>('analyst');
  readonly expanded = model(false);
  /** Approve / Correct / Reject clicked (the page runs SignalActionsService). */
  readonly act = output<SignalCardAction>();
  /** Show Approve / Correct / Reject (off for a rejected signal on the review page). */
  readonly actions = input(true);

  protected readonly terms = inject(JfrogTermsService).terms;

  protected readonly rankLabel = computed(() => {
    const r = this.rank();
    return r === null ? '' : String(r).padStart(2, '0');
  });
  /** Main company (companies before their products). */
  protected readonly company = computed(() => {
    const ents = this.signal().entities;
    const primary = ents.filter((e) => e.primary);
    const pool = primary.length ? primary : ents;
    return pool.find((e) => e.type === 'company') ?? pool[0] ?? null;
  });
  /** The enriched one-line takeaway; older signals fall back to the first sentence of "why". */
  protected readonly takeaway = computed(
    () => this.signal().takeaway || firstSentence(this.signal().why_jfrog_cares, 400),
  );
  protected readonly category = computed(() => categoryLabel(this.signal().category));
  protected readonly date = computed(() => shortDate(this.signal().published_at));
  protected readonly verification = computed(() => verificationLabel(this.signal().verification));
  protected readonly ids = computed(() => {
    const id = this.signal().id;
    return { title: `signal-${id}-title`, details: `signal-${id}-details` };
  });
  /** "Edited by analyst" (fields + who) or "Approved"; nothing before a review. */
  protected readonly reviewBadge = computed(() => {
    const r = this.signal().review;
    if (!r || r.status === 'new') return null;
    const by = r.by ? ` by ${r.by}` : '';
    const edited = r.edited_fields ?? [];
    if (edited.length) {
      return { label: 'Edited by analyst', detail: `${editedFieldsLabel(edited)} corrected${by}` };
    }
    if (r.status === 'rejected') return { label: 'Rejected', detail: `hidden from the Brief and lists${by}` };
    return { label: 'Approved', detail: `approved as is${by}` };
  });
  protected readonly toggleLabel = computed(
    () => `${this.expanded() ? 'Hide' : 'Show'} details for ${this.signal().title}`,
  );
  protected readonly sourceLabel = computed(() => {
    const s = this.signal();
    return s.source ? `Read on ${s.source.name}` : 'Read the original';
  });

  toggle(): void {
    this.expanded.set(!this.expanded());
  }

  /** Header row click; ignores text selection so copying a title doesn't toggle. */
  protected onHeaderClick(): void {
    if (globalThis.getSelection?.()?.toString()) return;
    this.toggle();
  }
}
