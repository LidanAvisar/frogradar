import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { IconComponent } from './icon.component';

/** Nothing to show (not an error). */
@Component({
  selector: 'fr-empty-state',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="state">
      <fr-icon name="inbox" [size]="22" />
      <p class="title">{{ title() }}</p>
      @if (message()) { <p class="small muted">{{ message() }}</p> }
    </div>
  `,
  styleUrl: './states.scss',
})
export class EmptyStateComponent {
  readonly title = input.required<string>();
  readonly message = input<string>('');
}

/** A request failed; offers a retry. Announced to screen readers. */
@Component({
  selector: 'fr-error-state',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="state error" role="alert">
      <fr-icon name="alert" [size]="22" />
      <p class="title">{{ title() }}</p>
      <p class="small muted">{{ message() }}</p>
      <button type="button" class="fr-button" (click)="retry.emit()">Try again</button>
    </div>
  `,
  styleUrl: './states.scss',
})
export class ErrorStateComponent {
  readonly title = input('Something went wrong');
  readonly message = input('The data could not be loaded. Check that the API is running.');
  readonly retry = output<void>();
}

/** Grey placeholder blocks while loading. */
@Component({
  selector: 'fr-skeleton',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (line of lines(); track $index) {
      <span class="bar" [style.width]="line"></span>
    }
  `,
  styles: [`
    :host { display: flex; flex-direction: column; gap: 10px; }
    .bar {
      display: block; height: 12px; border-radius: var(--fr-radius-pill);
      background: linear-gradient(90deg, var(--fr-divider) 0%, var(--fr-border) 50%, var(--fr-divider) 100%);
      background-size: 200% 100%; animation: shimmer 1.4s ease-in-out infinite;
    }
    @keyframes shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
  `],
})
export class SkeletonComponent {
  readonly lines = input<string[]>(['40%', '90%', '75%']);
}
