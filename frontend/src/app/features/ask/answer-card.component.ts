import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { AskAnswer } from '../../api/models';
import { plural } from '../../core/format';

export type Feedback = 'helpful' | 'wrong';

/**
 * One answer (docs/design Ask): header line, numbered points with citation chips (each chip jumps
 * to its entry in the Sources rail), "What it means for JFrog", and the analyst's actions. The
 * not-enough-evidence variant says so plainly, with what is missing and what to track.
 */
@Component({
  selector: 'fr-answer-card',
  standalone: true,
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <article class="card" [class.nee]="a().not_enough_evidence" [attr.aria-labelledby]="headId()">
      @if (a().not_enough_evidence) {
        <h3 class="head" [id]="headId()">Not enough evidence in FrogRadar</h3>
        @if (a().gap) { <p>{{ a().gap }}</p> }
        @if (a().suggestions.length) {
          <div class="next">
            <p class="eyebrow">What would help</p>
            <ul class="suggestions">
              @for (s of a().suggestions; track $index) { <li>{{ s }}</li> }
            </ul>
          </div>
        }
      } @else {
        <p class="head small" [id]="headId()">
          <span class="dot" aria-hidden="true"></span>
          Answered from {{ sourcesText() }} · every point cited
          @if (filterText()) { <span class="filters">· {{ filterText() }}</span> }
        </p>
        @if (a().intro) { <p class="intro">{{ a().intro }}</p> }
        <ol class="points">
          @for (p of a().points; track $index) {
            <li>
              <strong>{{ p.lead }}.</strong> {{ p.text }}
              @for (n of p.citations; track n) {
                <a class="chip mono" [href]="'#' + sourceId(n)" (click)="go($event, n)"
                   [attr.aria-label]="'Source ' + n + ': ' + title(n)">{{ n }}</a>
              }
            </li>
          }
        </ol>
        @if (a().meaning_for_jfrog; as m) {
          <div class="meaning">
            <p class="eyebrow accent">What it means for JFrog</p>
            <p>
              {{ m.text }}
              @for (n of m.citations; track n) {
                <a class="chip mono" [href]="'#' + sourceId(n)" (click)="go($event, n)"
                   [attr.aria-label]="'Source ' + n + ': ' + title(n)">{{ n }}</a>
              }
            </p>
          </div>
        }
      }
      <div class="actions">
        <button type="button" class="fr-button small-btn" [attr.aria-pressed]="feedback() === 'helpful'"
                (click)="rate.emit('helpful')">Helpful</button>
        <button type="button" class="fr-button small-btn" [attr.aria-pressed]="feedback() === 'wrong'"
                (click)="rate.emit('wrong')">Something's wrong</button>
        <a class="fr-button small-btn mission" routerLink="/missions" [queryParams]="{ turn: a().turn_id }">
          {{ a().not_enough_evidence ? 'Send a research mission' : 'Dig deeper with a mission' }}
        </a>
      </div>
    </article>
  `,
  styles: [`
    .card {
      display: flex; flex-direction: column; gap: 14px; padding: 20px 22px;
      background: var(--fr-surface); border: 1px solid var(--fr-border); border-radius: var(--fr-radius-card);
    }
    .card.nee { border-left: 4px solid var(--fr-changed-fg); }
    .head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--fr-muted); }
    h3.head { font-size: var(--fr-text-h2); color: var(--fr-ink); font-weight: 600; }
    .dot { width: 10px; height: 10px; border-radius: var(--fr-radius-pill); border: 2px solid var(--fr-accent); }
    .filters { color: var(--fr-ink-3); font-weight: 600; }
    .intro { color: var(--fr-ink); }
    .points { list-style: decimal; padding-left: 20px; display: flex; flex-direction: column; gap: 10px; color: var(--fr-ink-2); }
    .points strong { color: var(--fr-ink); }
    .chip {
      display: inline-block; margin-left: 3px; padding: 0 6px; border-radius: 4px; font-size: 12px; line-height: 1.6;
      background: var(--fr-accent-tint); color: var(--fr-accent-hover); text-decoration: none; vertical-align: 1px;
    }
    .chip:hover { background: var(--fr-accent); color: var(--fr-on-accent); }
    .meaning { background: var(--fr-why-bg); border-radius: var(--fr-radius-control); padding: 12px 14px; }
    .meaning p + p { margin-top: 4px; color: var(--fr-ink); }
    .accent { color: var(--fr-accent); }
    .next { display: flex; flex-direction: column; gap: 4px; }
    .suggestions { list-style: disc; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 10px; border-top: 1px solid var(--fr-divider); }
    .small-btn { min-height: 36px; padding: 0 12px; }
    @media (max-width: 720px) {
      .card { padding: var(--fr-gap); }
      .small-btn { min-height: var(--fr-touch); }
    }
  `],
})
export class AnswerCardComponent {
  readonly answer = input.required<AskAnswer>();
  readonly feedback = input<Feedback | null>(null);
  readonly cite = output<number>();
  readonly rate = output<Feedback>();

  protected readonly a = this.answer;
  protected readonly headId = computed(() => `answer-${this.a().turn_id}`);
  protected readonly sourcesText = computed(() => plural(this.a().sources.length, 'document'));
  protected readonly filterText = computed(() => {
    const f = this.a().filters;
    return [f.company, f.window].filter(Boolean).join(' · ');
  });

  /** The page moves focus to the Sources entry (a plain #link would resolve against <base href>). */
  protected go(event: Event, n: number): void {
    event.preventDefault();
    this.cite.emit(n);
  }

  sourceId(n: number): string {
    return `src-${this.a().turn_id}-${n}`;
  }

  title(n: number): string {
    return this.a().sources.find((s) => s.n === n)?.title ?? '';
  }
}
