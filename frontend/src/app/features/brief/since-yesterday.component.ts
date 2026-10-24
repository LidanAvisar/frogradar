import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { SinceYesterdayItem } from '../../api/models';
import { EmptyStateComponent } from '../../shared/states.component';
import { ImpactBadgeComponent } from '../../shared/impact-badge.component';
import { SectionCardComponent } from '../../shared/section-card.component';

/** Signals published in the last 24 hours (or on the latest available day), highest impact first. */
@Component({
  selector: 'fr-since-yesterday',
  standalone: true,
  imports: [EmptyStateComponent, ImpactBadgeComponent, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <fr-section-card [heading]="day() ? 'On ' + day() : 'What changed since yesterday'" headingId="since-yesterday-heading">
      <span card-meta class="mono small muted">{{ items().length }}</span>
      @if (items().length === 0) {
        <fr-empty-state title="Nothing new" [message]="day() ? 'No relevant signals were published that day.' : 'No relevant signals were published in the last 24 hours.'" />
      } @else {
        <ul class="list">
          @for (item of items(); track item.id) {
            <li>
              <fr-impact-badge [impact]="item.impact" [compact]="true" />
              <a [href]="item.url" target="_blank" rel="noopener noreferrer">
                {{ item.title }}<span class="visually-hidden"> (opens in a new tab)</span>
              </a>
            </li>
          }
        </ul>
      }
      <ng-content />
    </fr-section-card>
  `,
  styles: [`
    .list { display: flex; flex-direction: column; }
    li { display: flex; align-items: flex-start; gap: 10px; padding: 6px 0; border-top: 1px solid var(--fr-divider); }
    li:first-child { border-top: 0; }
    fr-impact-badge { margin-top: 3px; }
    a {
      display: block; min-height: 32px; padding: 2px 0;
      color: var(--fr-ink-2); font-size: var(--fr-text-small); font-weight: 500; text-decoration: none; line-height: 1.45;
    }
    a:hover { color: var(--fr-accent); text-decoration: underline; }
  `],
})
export class SinceYesterdayComponent {
  readonly items = input.required<SinceYesterdayItem[]>();
  /** Stale Brief: the latest available day ("21 Oct 2026"); null = the last 24 hours. */
  readonly day = input<string | null>(null);
}
