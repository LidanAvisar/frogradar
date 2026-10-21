import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { Evidence } from '../api/models';

/**
 * The evidence sentence from the source. Only a verified (verbatim) quote is shown.
 * side = before: the text was removed from the page → "Removed: '…'".
 */
@Component({
  selector: 'fr-evidence-quote',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (evidence(); as e) {
      @if (e.verified) {
        <figure class="quote" [class.compact]="compact()">
          @if (e.side === 'before') {
            <blockquote><span class="removed">Removed:</span> ‘{{ e.quote }}’</blockquote>
          } @else {
            <blockquote>“{{ e.quote }}”</blockquote>
          }
          @if (sourceName() || e.section) {
            <figcaption class="small muted">
              @if (sourceName()) { <span>Verbatim from {{ sourceName() }}</span> }
              @if (e.section) { <span class="section">{{ sourceName() ? ' · ' : '' }}{{ e.section }}</span> }
            </figcaption>
          }
        </figure>
      }
    }
  `,
  styles: [`
    .quote { border-left: 3px solid var(--fr-control-border); padding: 2px 0 2px 14px; }
    blockquote { color: var(--fr-ink-3); font-style: italic; }
    .compact blockquote { font-size: var(--fr-text-small); }
    .removed { font-style: normal; font-weight: 600; color: var(--fr-ink-2); }
    figcaption { margin-top: 4px; }
  `],
})
export class EvidenceQuoteComponent {
  readonly evidence = input<Evidence | null | undefined>(null);
  readonly sourceName = input<string | null | undefined>(null);
  readonly compact = input(false);
}
