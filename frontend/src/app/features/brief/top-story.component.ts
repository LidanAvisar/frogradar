import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { CitationRef, TopStory } from '../../api/models';
import { CompanyChipComponent } from '../../shared/company-chip.component';
import { ConfidenceBadgeComponent } from '../../shared/confidence-badge.component';
import { SourceLinkComponent } from '../../shared/source-link.component';

interface Row {
  label: string;
  text: string;
  cites: { n: number; ref: CitationRef }[];
}

/**
 * "Top story today": plain title, then What happened / Why it matters / Suggested move, each with
 * numbered links to the signals it relies on. Without a daily brief the API sends the top-ranked
 * signal (no suggested move) and the card says so.
 */
@Component({
  selector: 'fr-top-story',
  standalone: true,
  imports: [CompanyChipComponent, ConfidenceBadgeComponent, SourceLinkComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <article class="story" aria-labelledby="top-story-title">
      <p class="eyebrow label">Top story today</p>
      <h2 id="top-story-title" class="title">
        {{ story().title }}@for (c of titleCites(); track c.ref.id) {<sup class="cite"><a [href]="c.ref.url" target="_blank" rel="noopener noreferrer" [attr.title]="c.ref.title" [attr.aria-label]="'Source ' + c.n + ': ' + c.ref.title">{{ c.n }}</a></sup>}
      </h2>
      <div class="chips">
        @if (story().company; as company) { <fr-company-chip [name]="company" /> }
        @for (p of story().affects; track p) {
          <span class="affects small"><span class="affects-label">Affects</span> {{ p }}</span>
        }
        <fr-confidence-badge [confidence]="story().signal.confidence" />
      </div>
      <dl class="rows">
        @for (row of rows(); track row.label) {
          <div class="row">
            <dt class="eyebrow">{{ row.label }}</dt>
            <dd>
              {{ row.text }}@for (c of row.cites; track c.ref.id) {<sup class="cite"><a [href]="c.ref.url" target="_blank" rel="noopener noreferrer" [attr.title]="c.ref.title" [attr.aria-label]="'Source ' + c.n + ': ' + c.ref.title">{{ c.n }}</a></sup>}
            </dd>
          </div>
        }
      </dl>
      <footer class="footer small">
        <fr-source-link [url]="story().signal.url" [label]="story().signal.source?.name ?? 'Read the original'" />
        @if (story().source === 'top_signal') {
          <span class="muted">Highest-ranked signal · no daily brief yet</span>
        }
      </footer>
    </article>
  `,
  styles: [`
    .story {
      display: flex; flex-direction: column; gap: var(--fr-gap-sm);
      padding: var(--fr-card-pad); background: var(--fr-surface);
      border: 1px solid var(--fr-border); border-left: 4px solid var(--fr-accent);
      border-radius: var(--fr-radius-card);
    }
    .label { color: var(--fr-accent); }
    .title {
      font-size: var(--fr-text-headline); line-height: var(--fr-lh-headline);
      letter-spacing: var(--fr-ls-h1); font-weight: 600; color: var(--fr-ink);
    }
    .chips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px var(--fr-gap-sm); }
    .affects {
      display: inline-flex; align-items: center; gap: 4px; padding: 1px 10px;
      border-radius: var(--fr-radius-pill); background: var(--fr-accent-tint);
      color: var(--fr-accent); font-weight: 600; white-space: nowrap;
    }
    .affects-label { color: var(--fr-ink-3); font-weight: 500; }
    .rows { display: flex; flex-direction: column; gap: var(--fr-gap-sm); margin: 0; }
    .row {
      display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: var(--fr-gap-sm);
      align-items: baseline; padding-top: var(--fr-gap-sm); border-top: 1px solid var(--fr-divider);
    }
    dt { color: var(--fr-muted); }
    dd { margin: 0; color: var(--fr-ink); }
    .cite {
      font-family: var(--fr-font-mono); font-size: var(--fr-text-small); font-weight: 500;
      line-height: 0; margin-left: 2px;
    }
    .cite a { color: var(--fr-accent); text-decoration: none; padding: 0 2px; border-radius: var(--fr-radius-control); }
    .cite a:hover { color: var(--fr-accent-hover); text-decoration: underline; }
    .title .cite { font-size: var(--fr-text-small); vertical-align: super; }
    .footer { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--fr-gap); }
    @media (max-width: 720px) {
      .title { font-size: 22px; }
      .row { grid-template-columns: minmax(0, 1fr); gap: 4px; }
    }
  `],
})
export class TopStoryComponent {
  readonly story = input.required<TopStory>();

  /** Citation numbers in order of first use across the title and the rows. */
  private readonly numbers = computed(() => {
    const s = this.story();
    const order = new Map<number, number>();
    const refs = [
      ...s.title_citations,
      ...[s.what_happened, s.why_it_matters, s.suggested_move].flatMap((r) => r?.citations ?? []),
    ];
    for (const r of refs) if (!order.has(r.id)) order.set(r.id, order.size + 1);
    return order;
  });

  protected readonly titleCites = computed(() => this.cites(this.story().title_citations));

  protected readonly rows = computed<Row[]>(() => {
    const s = this.story();
    const rows: [string, typeof s.what_happened][] = [
      ['What happened', s.what_happened],
      ['Why it matters', s.why_it_matters],
      ['Suggested move', s.suggested_move ?? null],
    ];
    return rows
      .filter(([, r]) => !!r)
      .map(([label, r]) => ({ label, text: r!.text, cites: this.cites(r!.citations) }));
  });

  private cites(refs: CitationRef[]): { n: number; ref: CitationRef }[] {
    const numbers = this.numbers();
    return refs.map((ref) => ({ n: numbers.get(ref.id) ?? 0, ref }));
  }
}
