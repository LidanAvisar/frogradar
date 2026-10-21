import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { PipelineInfo } from '../../api/models';
import { dateTime } from '../../core/format';

/** Last pipeline run and what it watches. */
@Component({
  selector: 'fr-pipeline-status',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="status small">
      <p class="eyebrow">Last pipeline run</p>
      @if (info().last_run; as run) {
        <p class="run">
          <span [class]="'dot ' + run.status" aria-hidden="true"></span>
          <span class="mono">{{ when() }}</span>
          <span class="muted" aria-hidden="true">·</span>
          <span class="muted">{{ statusLabel() }}</span>
        </p>
      } @else {
        <p class="muted">No run yet</p>
      }
      <p class="muted">
        <span class="mono">{{ info().active_feeds }}</span> active feeds&nbsp;·&nbsp;<span class="mono">{{ info().watched_pages }}</span> watched pages
      </p>
    </div>
  `,
  styles: [`
    .status { display: flex; flex-direction: column; gap: 4px; }
    .run { display: flex; align-items: center; gap: 6px; color: var(--fr-ink-2); }
    .dot { width: 8px; height: 8px; border-radius: var(--fr-radius-pill); background: var(--fr-muted); }
    .dot.succeeded { background: var(--fr-accent); }
    .dot.failed { background: var(--fr-impact-5-fg); }
    .dot.partial, .dot.running { background: var(--fr-conf-medium-fg); }
  `],
})
export class PipelineStatusComponent {
  readonly info = input.required<PipelineInfo>();
  protected readonly when = computed(() => dateTime(this.info().last_run?.started_at));
  protected readonly statusLabel = computed(() => {
    const s = this.info().last_run?.status ?? '';
    return s.charAt(0).toUpperCase() + s.slice(1);
  });
}
