import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Card with an h2 header; the projected content is the body. */
@Component({
  selector: 'fr-section-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card" [attr.aria-labelledby]="headingId()">
      <header class="head">
        <h2 [id]="headingId()">{{ heading() }}</h2>
        <ng-content select="[card-meta]" />
      </header>
      <ng-content />
    </section>
  `,
  styles: [`
    .card {
      background: var(--fr-surface); border: 1px solid var(--fr-border);
      border-radius: var(--fr-radius-card); padding: var(--fr-card-pad-tight);
    }
    .head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--fr-gap-sm); margin-bottom: var(--fr-gap-sm); }
    h2 { font-size: var(--fr-text-h2); font-weight: 600; line-height: 1.3; }
  `],
})
export class SectionCardComponent {
  readonly heading = input.required<string>();
  readonly headingId = input.required<string>();
}
