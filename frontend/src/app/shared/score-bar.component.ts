import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { RadarBreakdown } from '../api/models';
import { scoreParts, scoreText } from './score';

/**
 * Emergence score: the number, a stacked bar of its components (decorative, aria-hidden) and the
 * same breakdown as text in a disclosure ("Relevance 28 of 35 · …"). `detailed` also shows one
 * sentence per component.
 */
@Component({
  selector: 'fr-score-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="head">
      <span class="score mono" [attr.aria-label]="'Emergence score ' + text() + ' of 100'">{{ text() }}</span>
      @if (parts().length) {
        <span class="bar" aria-hidden="true">
          @for (p of parts(); track p.key) {
            <span class="seg" [attr.data-key]="p.key" [style.width.%]="p.points" [attr.title]="p.label + ' ' + p.points + ' of ' + p.max"></span>
          }
        </span>
      }
    </div>
    @if (parts().length) {
      <details class="breakdown" [open]="detailed()">
        <summary class="small">Score breakdown</summary>
        <ul class="parts small">
          @for (p of parts(); track p.key) {
            <li>
              <span class="swatch" [attr.data-key]="p.key" aria-hidden="true"></span>
              <span class="label">{{ p.label }}</span>
              <span class="pts mono">{{ p.points }} <span class="muted">of {{ p.max }}</span></span>
              @if (detailed()) { <span class="why muted">{{ p.explanation }}</span> }
            </li>
          }
        </ul>
      </details>
    }
  `,
  styles: [`
    :host { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .head { display: flex; align-items: center; gap: var(--fr-gap-xs); }
    .score {
      font-size: var(--fr-text-body); font-weight: 600; color: var(--fr-ink);
      min-width: 2.2em; text-align: right;
    }
    .bar {
      display: flex; flex: 1; min-width: 72px; height: 8px; overflow: hidden;
      border-radius: var(--fr-radius-pill); background: var(--fr-score-track);
    }
    .seg { display: block; height: 100%; }
    [data-key='relevance'] { background: var(--fr-score-relevance); }
    [data-key='source_diversity'] { background: var(--fr-score-sources); }
    [data-key='mentions'] { background: var(--fr-score-mentions); }
    [data-key='mention_velocity'] { background: var(--fr-score-velocity); }
    [data-key='traction'] { background: var(--fr-score-traction); }
    [data-key='recency'] { background: var(--fr-score-recency); }
    summary {
      cursor: pointer; color: var(--fr-accent); font-weight: 600; min-height: 28px;
      display: inline-flex; align-items: center; border-radius: var(--fr-radius-control);
    }
    .parts { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
    li {
      display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; column-gap: 8px;
      align-items: baseline;
    }
    .swatch { width: 10px; height: 10px; border-radius: 2px; align-self: center; }
    .label { color: var(--fr-ink-2); }
    .pts { color: var(--fr-ink); white-space: nowrap; }
    .why { grid-column: 2 / -1; line-height: 1.45; }
  `],
})
export class ScoreBarComponent {
  readonly score = input<number | null | undefined>(null);
  readonly breakdown = input<RadarBreakdown | null | undefined>(null);
  readonly detailed = input(false);

  protected readonly text = computed(() => scoreText(this.score()));
  protected readonly parts = computed(() => scoreParts(this.breakdown()));
}
