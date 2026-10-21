import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

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
 * level links to its evidence signal; unknown cells offer "Investigate" (Coming soon). The table
 * scrolls horizontally inside its own box on narrow screens (focusable, labelled region).
 */
@Component({
  selector: 'fr-capability-matrix',
  standalone: true,
  imports: [LevelBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scroll" role="region" tabindex="0" [attr.aria-label]="'Capability matrix, JFrog vs ' + competitor() + ' (scrolls sideways)'">
      <table>
        <caption class="visually-hidden">Capability levels of JFrog and {{ competitor() }}, with evidence</caption>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">JFrog</th>
            <th scope="col">{{ competitor() }}</th>
            <th scope="col">Evidence</th>
          </tr>
        </thead>
        @for (g of groups(); track g.segment) {
          <tbody>
            @if (groups().length > 1) {
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
                    <button type="button" class="investigate fr-hint-host" aria-disabled="true" data-hint="Coming soon"
                            [attr.aria-label]="'Investigate ' + r.capability.name + ' (coming soon)'">Investigate →</button>
                  }
                </td>
              </tr>
            }
          </tbody>
        }
      </table>
    </div>
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
    .investigate {
      border: 0; background: none; padding: 0; min-height: 32px; cursor: not-allowed;
      font-size: var(--fr-text-small); font-weight: 600; color: var(--fr-muted);
    }
  `],
})
export class CapabilityMatrixComponent {
  readonly rows = input.required<MatrixRow[]>();
  readonly competitor = input.required<string>();

  protected readonly groups = computed<Group[]>(() => {
    const out: Group[] = [];
    for (const r of this.rows()) {
      let g = out.find((x) => x.segment === r.capability.segment);
      if (!g) {
        g = { segment: r.capability.segment, label: segmentLabel(r.capability.segment), rows: [] };
        out.push(g);
      }
      g.rows.push(r);
    }
    return out;
  });

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
