import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { IconComponent } from './icon.component';

/** Link to the original source, always in a new tab. */
@Component({
  selector: 'fr-source-link',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <a class="link" [href]="url()" target="_blank" rel="noopener noreferrer">
      <span>{{ label() }}</span>
      <fr-icon name="external" [size]="14" />
      <span class="visually-hidden">(opens in a new tab)</span>
    </a>
  `,
  styles: [`
    .link {
      display: inline-flex; align-items: center; gap: 4px; min-height: var(--fr-touch);
      font-weight: 600; font-size: var(--fr-text-small); text-decoration: none;
    }
    .link:hover span:first-child { text-decoration: underline; }
  `],
})
export class SourceLinkComponent {
  readonly url = input.required<string>();
  readonly label = input('Read the original');
}
