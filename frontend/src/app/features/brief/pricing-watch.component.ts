import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { PricingItem } from '../../api/models';
import { firstSentence, shortDate } from '../../core/format';
import { CompanyChipComponent } from '../../shared/company-chip.component';
import { EmptyStateComponent } from '../../shared/states.component';
import { EvidenceQuoteComponent } from '../../shared/evidence-quote.component';
import { SectionCardComponent } from '../../shared/section-card.component';
import { SourceLinkComponent } from '../../shared/source-link.component';

/** Latest change per watched pricing page: before → after prices when the diff pairs them. */
@Component({
  selector: 'fr-pricing-watch',
  standalone: true,
  imports: [
    CompanyChipComponent,
    EmptyStateComponent,
    EvidenceQuoteComponent,
    SectionCardComponent,
    SourceLinkComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <fr-section-card heading="Pricing watch" headingId="pricing-watch-heading">
      @if (items().length === 0) {
        <fr-empty-state title="No pricing changes" message="Watched pricing pages have not changed in the last 90 days." />
      } @else {
        <ul class="items">
          @for (item of items(); track item.signal.id) {
            <li class="item">
              <div class="row">
                @if (company(item); as name) { <fr-company-chip [name]="name" /> }
                <span class="mono small muted">{{ date(item.signal.published_at) }}</span>
              </div>
              <p class="headline">{{ item.signal.title }}</p>
              @if (item.changes.length) {
                <ul class="changes">
                  @for (c of item.changes; track $index) {
                    <li>
                      @if (c.label) { <span class="label small">{{ c.label }}</span> }
                      <span class="prices mono">
                        <s><span class="visually-hidden">was </span>{{ c.before }}</s>
                        <span aria-hidden="true">→</span>
                        <strong><span class="visually-hidden">now </span>{{ c.after }}</strong>
                      </span>
                    </li>
                  }
                </ul>
              } @else {
                <p class="summary small">{{ sentence(item.signal.summary) }}</p>
                <fr-evidence-quote [evidence]="item.signal.evidence" [compact]="true" />
              }
              <div class="notes small muted">
                <fr-source-link [url]="item.signal.url" [label]="item.signal.source?.name ?? 'Pricing page'" />
                @if (item.signal.page_diff; as pd) {
                  <span>{{ beforeNote(pd.before_origin, pd.before_captured_at) }}</span>
                }
              </div>
            </li>
          }
        </ul>
      }
    </fr-section-card>
  `,
  styles: [`
    .items { display: flex; flex-direction: column; }
    .item { display: flex; flex-direction: column; gap: 8px; padding: 14px 0; border-top: 1px solid var(--fr-divider); }
    .item:first-child { border-top: 0; padding-top: 0; }
    .item:last-child { padding-bottom: 0; }
    .row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .changes { display: flex; flex-direction: column; gap: 6px; }
    .label { display: block; color: var(--fr-muted); }
    .prices { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 6px; color: var(--fr-ink); }
    s { color: var(--fr-muted); }
    strong { font-weight: 500; }
    .summary { color: var(--fr-ink-2); }
    .headline { font-weight: 500; color: var(--fr-ink); line-height: var(--fr-lh-card-title); }
    .notes { display: flex; flex-direction: column; align-items: flex-start; }
    .notes fr-source-link { margin: -10px 0 -12px; }
  `],
})
export class PricingWatchComponent {
  readonly items = input.required<PricingItem[]>();

  protected company(item: PricingItem): string | null {
    const ents = item.signal.entities;
    const primary = ents.filter((e) => e.primary);
    const pool = primary.length ? primary : ents;
    return (pool.find((e) => e.type === 'company') ?? pool[0])?.name ?? null;
  }

  protected date(iso: string | null | undefined): string {
    return shortDate(iso);
  }

  protected sentence(text: string | null | undefined): string {
    return firstSentence(text, 200);
  }

  protected beforeNote(origin: string | null | undefined, at: string | null | undefined): string {
    if (!at) return '';
    const when = shortDate(at);
    return origin === 'wayback'
      ? `Compared with a Wayback Machine capture from ${when}`
      : `Compared with our capture from ${when}`;
  }
}
