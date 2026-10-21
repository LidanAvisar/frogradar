import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { Signal } from '../api/models';
import { categoryLabel, firstSentence, shortDate, verificationLabel } from '../core/format';
import { CompanyChipComponent } from './company-chip.component';
import { ConfidenceBadgeComponent } from './confidence-badge.component';
import { EvidenceQuoteComponent } from './evidence-quote.component';
import { IconComponent } from './icon.component';
import { ImpactBadgeComponent } from './impact-badge.component';
import { SourceLinkComponent } from './source-link.component';
import { VendorClaimTagComponent } from './vendor-claim-tag.component';

export type SignalCardVariant = 'analyst' | 'executive';

/** One signal. analyst = full card; executive = one line. */
@Component({
  selector: 'fr-signal-card',
  standalone: true,
  imports: [
    RouterLink,
    CompanyChipComponent,
    ConfidenceBadgeComponent,
    EvidenceQuoteComponent,
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

  protected readonly rankLabel = computed(() => {
    const r = this.rank();
    return r === null ? '' : String(r).padStart(2, '0');
  });
  protected readonly companies = computed(() => {
    const ents = this.signal().entities;
    const primary = ents.filter((e) => e.primary);
    const shown = primary.length ? primary : ents;
    return [...shown].sort((a, b) => Number(a.type !== 'company') - Number(b.type !== 'company')).slice(0, 2);
  });
  protected readonly category = computed(() => categoryLabel(this.signal().category));
  protected readonly date = computed(() => shortDate(this.signal().published_at));
  protected readonly verification = computed(() => verificationLabel(this.signal().verification));
  protected readonly oneLine = computed(() => firstSentence(this.signal().why_jfrog_cares, 140));
  protected readonly titleId = computed(() => `signal-${this.signal().id}-title`);
  protected readonly sourceLabel = computed(() => {
    const s = this.signal();
    return s.source ? `Read on ${s.source.name}` : 'Read the original';
  });
}
