import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Company / product name chip. */
@Component({
  selector: 'fr-company-chip',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="chip">{{ name() }}</span>`,
  styles: [`
    .chip {
      display: inline-flex; align-items: center; padding: 1px 10px;
      border: 1px solid var(--fr-control-border); border-radius: var(--fr-radius-pill);
      background: var(--fr-surface); color: var(--fr-ink);
      font-size: var(--fr-text-small); font-weight: 600; white-space: nowrap; line-height: 1.5;
    }
  `],
})
export class CompanyChipComponent {
  readonly name = input.required<string>();
}
