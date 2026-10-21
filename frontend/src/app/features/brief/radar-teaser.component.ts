import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { RadarTeaser } from '../../api/models';
import { IconComponent } from '../../shared/icon.component';
import { EmptyStateComponent } from '../../shared/states.component';
import { SectionCardComponent } from '../../shared/section-card.component';

/**
 * "New on the radar": the top players the system found that nobody configured (emergence score,
 * sources count, one line from the verified profile). Links to the Radar page.
 */
@Component({
  selector: 'fr-radar-teaser',
  standalone: true,
  imports: [EmptyStateComponent, IconComponent, RouterLink, SectionCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <fr-section-card heading="New on the radar" headingId="radar-heading">
      <a card-meta class="view small" routerLink="/radar">View radar<fr-icon name="arrow" [size]="14" /></a>
      @if (items().length === 0) {
        <fr-empty-state title="Nothing new" message="No new players were discovered yet." />
      } @else {
        <ul class="list">
          @for (item of items(); track item.id) {
            <li>
              <div class="row">
                <a class="name" [routerLink]="['/radar', item.id]">{{ item.name }}</a>
                @if (item.status === 'watching') {
                  <span class="badge">Watching</span>
                }
                <span class="score mono" [attr.aria-label]="'Emergence score ' + scoreText(item.score)">{{ scoreText(item.score) }}</span>
              </div>
              @if (item.line) {
                <p class="line small">{{ item.line }}</p>
              }
              <p class="meta small muted">{{ item.sources_count }} {{ item.sources_count === 1 ? 'source' : 'sources' }}</p>
            </li>
          }
        </ul>
      }
    </fr-section-card>
  `,
  styles: [`
    .view {
      display: inline-flex; align-items: center; gap: 4px; min-height: 32px;
      color: var(--fr-accent); font-weight: 600; text-decoration: none;
    }
    .view:hover { text-decoration: underline; }
    .list { display: flex; flex-direction: column; }
    li { padding: 10px 0; border-top: 1px solid var(--fr-divider); }
    li:first-child { border-top: 0; padding-top: 2px; }
    .row { display: flex; align-items: center; gap: var(--fr-gap-xs); }
    .name {
      color: var(--fr-ink); font-weight: 600; text-decoration: none; min-height: 32px;
      display: inline-flex; align-items: center; overflow-wrap: anywhere;
    }
    .name:hover { color: var(--fr-accent); text-decoration: underline; }
    .badge {
      font-size: var(--fr-text-eyebrow); font-weight: 600; letter-spacing: var(--fr-ls-eyebrow);
      text-transform: uppercase; color: var(--fr-accent); background: var(--fr-accent-tint);
      border-radius: var(--fr-radius-pill); padding: 2px 8px;
    }
    .score {
      margin-left: auto; font-size: var(--fr-text-small); font-weight: 600; color: var(--fr-ink-2);
      background: var(--fr-impact-low-bg); border-radius: var(--fr-radius-pill); padding: 2px 8px;
    }
    .line {
      color: var(--fr-ink-2); line-height: 1.45; margin-top: 2px;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .meta { margin-top: 2px; }
  `],
})
export class RadarTeaserComponent {
  readonly items = input.required<RadarTeaser[]>();

  scoreText(score: number | null | undefined): string {
    return score == null ? '–' : Math.round(score).toString();
  }
}
