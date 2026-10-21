import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Impact 1-5 as a pill. compact = number only (lists). */
@Component({
  selector: 'fr-impact-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (impact() !== null) {
      <span [class]="classes()" [attr.aria-label]="'Impact ' + impact() + ' of 5'">
        <span aria-hidden="true">{{ compact() ? impact() : 'Impact ' + impact() }}</span>
      </span>
    }
  `,
  styles: [`
    .pill {
      display: inline-flex; align-items: center; justify-content: center;
      padding: 2px 10px; border-radius: var(--fr-radius-pill);
      font-size: var(--fr-text-small); font-weight: 500; white-space: nowrap; line-height: 1.5;
    }
    /* Urgent (4-5): a little larger and bolder; 1-3 stay quiet. */
    .strong { font-size: var(--fr-text-pill-strong); font-weight: 700; padding: 3px 12px; }
    .compact { min-width: 24px; padding: 1px 6px; }
    .compact.strong { font-size: var(--fr-text-small); padding: 1px 6px; }
    .level-5 { background: var(--fr-impact-5-bg); color: var(--fr-impact-5-fg); }
    .level-4 { background: var(--fr-impact-4-bg); color: var(--fr-impact-4-fg); }
    .level-3 { background: var(--fr-impact-3-bg); color: var(--fr-impact-3-fg); }
    .level-low { background: var(--fr-impact-low-bg); color: var(--fr-impact-low-fg); }
  `],
})
export class ImpactBadgeComponent {
  readonly impact = input<number | null | undefined>(null);
  readonly compact = input(false);
  protected readonly classes = computed(() => {
    const i = this.impact() ?? 0;
    const level = i >= 5 ? '5' : i === 4 ? '4' : i === 3 ? '3' : 'low';
    const strong = i >= 4 ? ' strong' : '';
    return `pill mono level-${level}${strong}${this.compact() ? ' compact' : ''}`;
  });
}
