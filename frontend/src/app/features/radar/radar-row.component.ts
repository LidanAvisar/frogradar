import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { RadarCandidate } from '../../api/models';
import { kindLabel, plural, radarStatusInfo, relationInfo, relativeDays, shortDate } from '../../core/format';
import { ScoreBarComponent } from '../../shared/score-bar.component';

export type RadarAction = 'watch' | 'track' | 'dismiss';

/** One radar candidate in the list: what it is, why now, JFrog relation, score with breakdown, actions. */
@Component({
  selector: 'fr-radar-row',
  standalone: true,
  imports: [RouterLink, ScoreBarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <article class="row" [attr.aria-labelledby]="'cand-' + c().id">
      <div class="main">
        <div class="title-line">
          <h3 class="name" [id]="'cand-' + c().id"><a [routerLink]="['/radar', c().id]">{{ c().name }}</a></h3>
          <span class="fr-pill">{{ kind() }}</span>
          <span [class]="'fr-pill tone-' + status().tone">{{ status().label }}</span>
        </div>
        @if (c().relation; as rel) {
          <p class="relation">
            <span [class]="'fr-pill tone-' + relation().tone">{{ relation().label }}</span>
            @if (c().relation_products?.length) {
              <span class="small products">{{ c().relation_products?.join(', ') }}</span>
            }
            @if (c().relation_check === 'partial') {
              <span class="small muted" title="The verifier found the core of the relation statement right but a detail overstated">partly verified</span>
            }
          </p>
        }
        @if (c().what_it_is) {
          <p class="line"><span class="line-label">What it is</span> {{ c().what_it_is }}</p>
        }
        @if (c().why_notable_now) {
          <p class="line"><span class="line-label">Why now</span> {{ c().why_notable_now }}</p>
        }
        @if (!c().what_it_is && !c().why_notable_now && c().line) {
          <p class="line"><span class="line-label">Context</span> {{ c().line }}</p>
        }
        <p class="meta small muted">
          {{ sources() }} · {{ mentions() }} · first seen <time [attr.datetime]="c().first_seen" [attr.title]="firstSeenDate()">{{ firstSeen() }}</time>
        </p>
      </div>
      <div class="side">
        <fr-score-bar [score]="c().score" [breakdown]="c().breakdown" />
        @if (open()) {
          <div class="actions">
            @if (c().status === 'new') {
              <button type="button" class="fr-button" (click)="act.emit('watch')" [attr.aria-label]="'Watch ' + c().name">Watch</button>
            }
            <button type="button" class="fr-button" (click)="act.emit('track')" [attr.aria-label]="'Track ' + c().name">Track</button>
            <button type="button" class="fr-button" (click)="act.emit('dismiss')" [attr.aria-label]="'Dismiss ' + c().name">Dismiss</button>
          </div>
        }
      </div>
    </article>
  `,
  styles: [`
    .row {
      display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: var(--fr-gap-lg);
      padding: var(--fr-gap) var(--fr-card-pad-tight); background: var(--fr-surface);
      border: 1px solid var(--fr-border); border-radius: var(--fr-radius-card);
    }
    .main { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .title-line { display: flex; flex-wrap: wrap; align-items: center; gap: 6px var(--fr-gap-xs); }
    .name { font-size: var(--fr-text-card-title); line-height: var(--fr-lh-card-title); font-weight: 600; }
    .name a { color: var(--fr-ink); text-decoration: none; overflow-wrap: anywhere; }
    .name a:hover { color: var(--fr-accent); text-decoration: underline; }
    .relation { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .products { color: var(--fr-ink-3); font-weight: 600; }
    .line { color: var(--fr-ink-2); line-height: 1.45; }
    .line-label {
      font-size: var(--fr-text-eyebrow); font-weight: 700; letter-spacing: var(--fr-ls-eyebrow);
      text-transform: uppercase; color: var(--fr-muted); margin-right: 4px;
    }
    .side { display: flex; flex-direction: column; gap: var(--fr-gap-sm); min-width: 0; }
    .actions { display: flex; flex-wrap: wrap; gap: 6px; }
    .actions .fr-button { min-height: 36px; padding: 0 12px; }
    @media (max-width: 720px) {
      .row { grid-template-columns: minmax(0, 1fr); gap: var(--fr-gap-sm); padding: var(--fr-gap); }
      .actions .fr-button { min-height: var(--fr-touch); flex: 1; }
    }
  `],
})
export class RadarRowComponent {
  readonly candidate = input.required<RadarCandidate>();
  readonly act = output<RadarAction>();

  protected readonly c = this.candidate;
  protected readonly kind = computed(() => kindLabel(this.c().kind));
  protected readonly status = computed(() => radarStatusInfo(this.c().status));
  protected readonly relation = computed(() => relationInfo(this.c().relation));
  protected readonly open = computed(() => this.c().status === 'new' || this.c().status === 'watching');
  protected readonly sources = computed(() => plural(this.c().sources_count, 'source'));
  protected readonly mentions = computed(() => plural(this.c().mentions_count, 'mention'));
  protected readonly firstSeen = computed(() => relativeDays(this.c().first_seen));
  protected readonly firstSeenDate = computed(() => shortDate(this.c().first_seen));
}
