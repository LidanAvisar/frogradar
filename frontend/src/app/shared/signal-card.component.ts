import { ChangeDetectionStrategy, Component, computed, inject, input, model } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { Signal } from '../api/models';
import { categoryLabel, firstSentence, shortDate, verificationLabel } from '../core/format';
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
  protected readonly takeaway = computed(() => firstSentence(this.signal().why_jfrog_cares, 400));
  protected readonly category = computed(() => categoryLabel(this.signal().category));
  protected readonly date = computed(() => shortDate(this.signal().published_at));
  protected readonly verification = computed(() => verificationLabel(this.signal().verification));
  protected readonly ids = computed(() => {
    const id = this.signal().id;
    return { title: `signal-${id}-title`, details: `signal-${id}-details`, soon: `signal-${id}-soon` };
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
