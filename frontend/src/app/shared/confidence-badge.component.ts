import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { Confidence } from '../api/models';

/** Confidence band + score, e.g. "High · 0.83". */
@Component({
  selector: 'fr-confidence-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (confidence(); as c) {
      <span [class]="'pill band-' + c.band"
            [attr.aria-label]="'Confidence ' + c.band + ', ' + score()">
        <span class="inner" aria-hidden="true"><span>{{ label() }}</span><span>·</span><span class="mono">{{ score() }}</span></span>
      </span>
    }
  `,
  styles: [`
    .pill {
      display: inline-flex; align-items: center; padding: 2px 10px;
      border-radius: var(--fr-radius-pill); font-size: var(--fr-text-small); font-weight: 600;
      white-space: nowrap; line-height: 1.5;
    }
    .inner { display: inline-flex; gap: 5px; }
    .mono { font-weight: 500; }
    .band-high { background: var(--fr-conf-high-bg); color: var(--fr-conf-high-fg); }
    .band-medium { background: var(--fr-conf-medium-bg); color: var(--fr-conf-medium-fg); }
    .band-low { background: var(--fr-conf-low-bg); color: var(--fr-conf-low-fg); }
  `],
})
export class ConfidenceBadgeComponent {
  readonly confidence = input<Confidence | null | undefined>(null);
  protected readonly score = computed(() => (this.confidence()?.score ?? 0).toFixed(2));
  protected readonly label = computed(() => {
    const b = this.confidence()?.band ?? '';
    return b.charAt(0).toUpperCase() + b.slice(1);
  });
}
