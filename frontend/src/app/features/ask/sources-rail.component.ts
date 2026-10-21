import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { AskSource } from '../../api/models';
import { shortDate } from '../../core/format';

const TYPE_LABELS: Record<string, string> = {
  pricing_page: 'Pricing page',
  battlecard: 'Battlecard',
  radar: 'Radar',
};

/** The cited documents of one answer: number · type, confidence, title link, source and date. */
@Component({
  selector: 'fr-sources-rail',
  standalone: true,
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 class="title">Sources</h2>
    @if (sources().length === 0) {
      <p class="small muted">No documents were cited.</p>
    }
    @for (s of sources(); track s.n) {
      <div class="src" [id]="'src-' + turnId() + '-' + s.n" tabindex="-1">
        <div class="top">
          <span class="mono kind">{{ s.n }} · {{ typeLabel(s) }}</span>
          @if (s.confidence_band) {
            <span class="conf">{{ band(s.confidence_band) }}@if (s.confidence != null) { · {{ s.confidence.toFixed(2) }} }</span>
          }
        </div>
        @if (s.type === 'article' || s.type === 'pricing_page') {
          <a class="name" [href]="s.url" target="_blank" rel="noopener noreferrer">{{ s.title }}</a>
        } @else {
          <a class="name" [routerLink]="s.url">{{ s.title }}</a>
        }
        <p class="small muted">{{ s.source }}@if (s.date) { · {{ date(s.date) }} }</p>
      </div>
    }
    <p class="small muted foot">Answers use only stored intelligence. Summaries and short quotes are shown; full article text never is.</p>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .title { font-size: var(--fr-text-body); font-weight: 700; }
    .src {
      display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; background: var(--fr-surface);
      border: 1px solid var(--fr-border); border-radius: var(--fr-radius-card);
    }
    .src:target, .src:focus { border-color: var(--fr-accent); box-shadow: var(--fr-focus-ring); }
    .top { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
    .kind { color: var(--fr-muted); text-transform: uppercase; }
    .conf { font-weight: 600; color: var(--fr-accent); }
    .name { font-weight: 600; color: var(--fr-ink); text-decoration: none; overflow-wrap: anywhere; }
    .name:hover { color: var(--fr-accent); text-decoration: underline; }
    .foot { margin-top: 4px; }
  `],
})
export class SourcesRailComponent {
  readonly sources = input.required<AskSource[]>();
  readonly turnId = input.required<number>();

  typeLabel(s: AskSource): string {
    if (s.type === 'article') return s.source_kind ?? 'Article';
    return TYPE_LABELS[s.type] ?? s.type;
  }

  band(b: string): string {
    return b.charAt(0).toUpperCase() + b.slice(1);
  }

  date(iso: string): string {
    return shortDate(iso);
  }
}
