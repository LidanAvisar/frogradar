import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatDialogActions, MatDialogContent, MatDialogRef, MatDialogTitle } from '@angular/material/dialog';

import { StatusService, usdShort } from '../core/status.service';

const TONE: Record<string, string> = { enabled: 'tone-green', degraded: 'tone-yellow', off: 'tone-red' };

/** System status: keys present (never values), features, today's LLM spend, costs. */
@Component({
  selector: 'fr-status-dialog',
  standalone: true,
  imports: [MatDialogActions, MatDialogContent, MatDialogTitle],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title class="title">System status</h2>
    <mat-dialog-content class="body">
      @if (status(); as s) {
        <p class="small">
          LLM spend today {{ usd(s.spend_today_usd) }} of {{ usd(s.daily_budget_usd) }}
          (daily cap, LLM_DAILY_BUDGET_USD in .env).
        </p>
        <section aria-labelledby="st-features">
          <h3 id="st-features" class="eyebrow">Features</h3>
          <ul class="rows">
            @for (f of s.features; track f.name) {
              <li>
                <span class="fr-pill" [class]="tone(f.state)">{{ f.state }}</span>
                <span><strong>{{ f.label }}</strong>@if (f.reason) { <span class="muted"> – {{ f.reason }}</span> }</span>
              </li>
            }
          </ul>
        </section>
        <section aria-labelledby="st-keys">
          <h3 id="st-keys" class="eyebrow">Keys (present or missing, never shown)</h3>
          <ul class="rows">
            @for (m of s.models; track m.tier) {
              <li>
                <span class="fr-pill" [class]="'fr-pill ' + (m.key_present ? 'tone-green' : 'tone-red')">{{ m.key_present ? 'ok' : 'missing' }}</span>
                <span><strong>{{ m.tier }}</strong>&nbsp;<span class="mono small">{{ m.model }}</span>
                  @if (!m.key_present) { <span class="muted"> – {{ m.env_vars.join(', ') }}</span> }</span>
              </li>
            }
            @for (k of extraKeys(); track k.name) {
              <li>
                <span class="fr-pill" [class]="'fr-pill ' + (k.ok ? 'tone-green' : '')">{{ k.ok ? 'ok' : 'not set' }}</span>
                <span class="mono small">{{ k.name }}</span> <span class="muted small">(optional)</span>
              </li>
            }
          </ul>
        </section>
        @for (w of s.warnings; track w) {
          <p class="small warn" role="note">{{ w }}</p>
        }
        <section aria-labelledby="st-costs">
          <h3 id="st-costs" class="eyebrow">What things cost (measured)</h3>
          <p class="small">
            Ask a new question ≈ {{ usd(s.cost_estimates_usd.ask) }} (repeats are cached: $0) ·
            research mission ≈ {{ usd(s.cost_estimates_usd.mission) }} · daily pipeline run ≈
            {{ usd(s.cost_estimates_usd.daily_run) }}
          </p>
        </section>
      } @else {
        <p class="small muted">Status could not be loaded.</p>
      }
    </mat-dialog-content>
    <mat-dialog-actions class="actions">
      <button type="button" class="fr-button" (click)="ref.close()">Close</button>
    </mat-dialog-actions>
  `,
  styleUrls: ['../features/radar/dialog.scss'],
  styles: `
    .rows { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 0; }
    .rows li { display: flex; gap: 8px; align-items: baseline; }
    .rows .fr-pill { min-width: 64px; justify-content: center; }
    .warn { color: var(--fr-claim-fg); background: var(--fr-claim-bg); padding: 6px 10px; border-radius: var(--fr-radius-control); }
    h3 { margin: 0; }
  `,
})
export class StatusDialogComponent {
  protected readonly ref = inject<MatDialogRef<StatusDialogComponent>>(MatDialogRef);
  private readonly svc = inject(StatusService);
  protected readonly status = this.svc.status;
  protected readonly usd = usdShort;

  constructor() {
    this.svc.refresh();
  }

  protected tone(state: string): string {
    return `fr-pill ${TONE[state] ?? ''}`;
  }

  protected extraKeys(): { name: string; ok: boolean }[] {
    return Object.entries(this.status()?.extra_keys ?? {}).map(([name, ok]) => ({ name, ok }));
  }
}
