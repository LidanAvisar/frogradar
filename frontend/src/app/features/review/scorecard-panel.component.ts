import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { Scorecard } from '../../api/models';
import { signalReasonLabel } from '../../core/format';

/** AI vs Analyst: how often analysts accept, correct or reject what the pipeline produced. */
@Component({
  selector: 'fr-scorecard-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="fr-panel card" aria-labelledby="scorecard-heading">
      <div class="head">
        <h2 id="scorecard-heading" class="eyebrow">AI vs Analyst</h2>
        @if (week(); as w) { <span class="small muted">This week: {{ w.reviewed }} reviewed</span> }
      </div>
      @if (stats(); as s) {
        @if (s.reviewed) {
          <ul class="tiles" role="list">
            <li class="tile"><span class="n mono">{{ s.reviewed }}</span><span class="label">reviewed</span></li>
            <li class="tile"><span class="n mono">{{ s.approved_pct }}%</span><span class="label">approved as is</span></li>
            <li class="tile"><span class="n mono">{{ s.corrected_pct }}%</span><span class="label">corrected</span></li>
            <li class="tile"><span class="n mono">{{ s.rejected_pct }}%</span><span class="label">rejected</span></li>
          </ul>
          <p class="fields small">
            <span>Corrected fields:</span>
            <span>category <b class="mono">{{ pct(s.fields.category.rate) }}</b></span>
            <span>impact <b class="mono">{{ pct(s.fields.impact.rate) }}</b>@if (s.fields.impact.mean_delta != null) { <span class="muted"> (avg {{ delta(s.fields.impact.mean_delta) }})</span> }</span>
            <span>relevance <b class="mono">{{ pct(s.fields.relevance.rate) }}</b></span>
            <span>company <b class="mono">{{ pct(s.fields.entity.rate) }}</b></span>
          </p>
          @if (s.reasons.length) {
            <p class="reasons small">
              <span>Top reasons:</span>
              @for (r of s.reasons; track r.code) { <span class="fr-pill">{{ label(r.code) }} · {{ r.count }}</span> }
            </p>
          }
        } @else {
          <p class="small muted">No reviews yet. Approve, correct or reject signals below and the agreement shows here.</p>
        }
      }
    </section>
  `,
  styles: [`
    .card { display: flex; flex-direction: column; gap: var(--fr-gap-sm); }
    .head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: var(--fr-gap-xs); }
    .head h2 { margin: 0; }
    .tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--fr-gap-xs); }
    .tile { display: flex; flex-direction: column; padding: 8px 12px; border: 1px solid var(--fr-border); border-radius: var(--fr-radius-card); background: var(--fr-page); }
    .n { font-size: var(--fr-text-h2); font-weight: 600; color: var(--fr-accent); line-height: 1.2; }
    .label { font-size: var(--fr-text-small); color: var(--fr-ink-3); }
    .fields, .reasons { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; color: var(--fr-ink-2); }
    .reasons { gap: 6px; }
    @media (max-width: 520px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  `],
})
export class ScorecardPanelComponent {
  readonly scorecard = input<Scorecard | null>(null);
  protected readonly stats = computed(() => this.scorecard()?.overall ?? null);
  protected readonly week = computed(() => this.scorecard()?.weeks[0] ?? null);

  protected pct(rate: number): string {
    return `${Math.round(rate * 100)}%`;
  }

  protected delta(d: number): string {
    return `${d > 0 ? '+' : d < 0 ? '−' : ''}${Math.abs(d)}`;
  }

  protected label(code: string): string {
    return signalReasonLabel(code);
  }
}
