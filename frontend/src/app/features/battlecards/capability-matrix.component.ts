import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { MatrixRow } from '../../api/models';
import { firstSentence, segmentLabel, shortDate } from '../../core/format';
import { LevelBadgeComponent } from '../../shared/level-badge.component';

interface Group {
  segment: string;
  label: string;
  rows: MatrixRow[];
}

/**
 * JFrog vs competitor per capability, grouped by segment. Levels are symbol + word; each competitor
 * level links to its evidence signal. Capabilities where the competitor's level is unknown are folded
 * into a "without evidence yet" disclosure (JFrog's level and an Investigate link per row that starts a research mission).
 * Tables scroll horizontally inside their own box on narrow screens (focusable, labelled region).
 */
@Component({
  selector: 'fr-capability-matrix',
  standalone: true,
  imports: [LevelBadgeComponent, NgTemplateOutlet, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (known().length) {
      <ng-container *ngTemplateOutlet="table; context: { $implicit: groupsOf(known()), caption: 'with evidence', label: '' }" />
    } @else {
      <p class="small muted none-known">No {{ competitor() }} capability has evidence yet.</p>
    }
    @if (unknown().length) {
      <details class="rest">
        <summary>
          <span class="rest-title">{{ unknown().length }} {{ unknown().length === 1 ? 'capability' : 'capabilities' }} without evidence yet</span>
          <span class="small muted">{{ competitor() }}'s level is unknown; JFrog's level is shown</span>
        </summary>
        <ng-container *ngTemplateOutlet="table; context: { $implicit: groupsOf(unknown()), caption: 'without evidence yet', label: ' without evidence' }" />
      </details>
    }

    <ng-template #table let-groups let-caption="caption" let-label="label">
      <div class="scroll" role="region" tabindex="0" [attr.aria-label]="'Capability matrix' + label + ', JFrog vs ' + competitor() + ' (scrolls sideways)'">
        <table>
          <caption class="visually-hidden">Capability levels of JFrog and {{ competitor() }}, {{ caption }}</caption>
          <thead>
            <tr>
              <th scope="col">Capability</th>
              <th scope="col">JFrog</th>
              <th scope="col">{{ competitor() }}</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          @for (g of groups; track g.segment) {
            <tbody>
              @if (groups.length > 1) {
                <tr class="segment"><th scope="colgroup" colspan="4">{{ g.label }}</th></tr>
              }
              @for (r of g.rows; track r.capability.slug) {
                <tr [class.changed]="r.changed_recently">
                  <th scope="row" class="cap">
                    {{ r.capability.name }}
                    @if (r.changed_recently) { <span class="changed-tag">Changed recently</span> }
                  </th>
                  <td>
                    <fr-level-badge [level]="r.jfrog.level" />
                    @if (r.jfrog.note && r.jfrog.level !== 'unknown') {
                      <span class="note" [attr.title]="r.jfrog.note">{{ short(r.jfrog.note) }}</span>
                    }
                  </td>
                  <td>
                    <fr-level-badge [level]="r.competitor.level" />
                    @if (r.competitor.note) { <span class="note" [attr.title]="r.competitor.note">{{ short(r.competitor.note) }}</span> }
                  </td>
                  <td class="evidence">
                    @if (r.competitor.evidence; as ev) {
                      <a [href]="ev.url" target="_blank" rel="noopener noreferrer" [attr.title]="ev.title">{{ evidenceText(ev.title, ev.published_at) }}</a>
                    } @else {
                      <a class="investigate" routerLink="/missions"
                         [queryParams]="{ entity_slug: competitorSlug(), capability_slug: r.capability.slug }"
                         [attr.aria-label]="'Investigate ' + r.capability.name + ' with a research mission'">Investigate</a>
                    }
                  </td>
                </tr>
              }
            </tbody>
          }
        </table>
      </div>
    </ng-template>
  `,
  styles: [`
    .scroll { overflow-x: auto; margin-top: var(--fr-gap-sm); border-radius: var(--fr-radius-control); }
    table { width: 100%; min-width: 600px; border-collapse: collapse; font-size: 14px; }
    thead th {
      text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--fr-border);
      font-size: var(--fr-text-eyebrow); letter-spacing: 0.04em; text-transform: uppercase;
      color: var(--fr-muted); font-weight: 600;
    }
    td, tbody th { padding: 12px 10px; border-bottom: 1px solid var(--fr-divider); vertical-align: top; text-align: left; }
    .cap { font-weight: 500; color: var(--fr-ink); width: 34%; }
    .segment th {
      padding: 14px 10px 6px; font-size: var(--fr-text-eyebrow); font-weight: 700;
      letter-spacing: var(--fr-ls-eyebrow); text-transform: uppercase; color: var(--fr-muted);
      border-bottom: 1px solid var(--fr-border);
    }
    tr.changed { background: var(--fr-changed-bg); }
    .changed-tag { display: block; font-size: 12px; font-weight: 600; color: var(--fr-changed-fg); }
    .note { display: block; font-size: var(--fr-text-small); color: var(--fr-muted); line-height: 1.4; margin-top: 2px; }
    .evidence a { font-size: var(--fr-text-small); }
    .none-known { margin-top: var(--fr-gap-sm); }
    .rest { margin-top: var(--fr-gap); }
    .rest summary {
      display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--fr-gap-sm);
      min-height: var(--fr-touch); padding: 10px 12px; cursor: pointer;
      border: 1px solid var(--fr-border); border-radius: var(--fr-radius-control); background: var(--fr-page);
    }
    .rest summary::before { content: '▸'; color: var(--fr-muted); }
    .rest[open] summary::before { content: '▾'; }
    .rest-title { font-weight: 600; color: var(--fr-ink); }
    .investigate {
      display: inline-flex; align-items: center; min-height: 32px;
      font-size: var(--fr-text-small); font-weight: 600; color: var(--fr-accent);
    }
  `],
})
export class CapabilityMatrixComponent {
  readonly rows = input.required<MatrixRow[]>();
  readonly competitor = input.required<string>();
  readonly competitorSlug = input<string>('');

  /** Rows with an evidenced competitor level are shown; the rest are folded. */
  protected readonly known = computed(() => this.rows().filter((r) => r.competitor.level !== 'unknown'));
  protected readonly unknown = computed(() => this.rows().filter((r) => r.competitor.level === 'unknown'));

  protected groupsOf(rows: MatrixRow[]): Group[] {
    const out: Group[] = [];
    for (const r of rows) {
      let g = out.find((x) => x.segment === r.capability.segment);
      if (!g) {
        g = { segment: r.capability.segment, label: segmentLabel(r.capability.segment), rows: [] };
        out.push(g);
      }
      g.rows.push(r);
    }
    return out;
  }

  protected short(note: string): string {
    // Short label; the full note is the cell's tooltip.
    const s = firstSentence(note, 200).replace(/\s*[:(].*$/, '');
    return s.length > 48 ? `${s.slice(0, 47).trimEnd()}…` : s;
  }

  protected evidenceText(title: string, date: string | null | undefined): string {
    const t = title.length > 48 ? `${title.slice(0, 47).trimEnd()}…` : title;
    return date ? `${t} · ${shortDate(date)}` : t;
  }
}
