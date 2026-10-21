import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export const LEVELS: Record<string, { symbol: string; label: string }> = {
  full: { symbol: '●', label: 'Full' },
  partial: { symbol: '◐', label: 'Partial' },
  planned: { symbol: '◔', label: 'Planned' },
  none: { symbol: '✕', label: 'None' },
  unknown: { symbol: '○', label: 'Unknown' },
};

/** Capability level as a symbol AND a word (never color alone). The symbol is decorative. */
@Component({
  selector: 'fr-level-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="level" [attr.data-level]="key()"><span class="sym" aria-hidden="true">{{ info().symbol }}</span> {{ info().label }}</span>`,
  styles: [`
    .level { font-weight: 600; white-space: nowrap; }
    [data-level='full'] { color: var(--fr-level-full); }
    [data-level='partial'] { color: var(--fr-level-partial); }
    [data-level='planned'] { color: var(--fr-level-planned); }
    [data-level='none'] { color: var(--fr-level-none); }
    [data-level='unknown'] { color: var(--fr-level-unknown); font-weight: 500; }
  `],
})
export class LevelBadgeComponent {
  readonly level = input.required<string>();
  protected readonly key = computed(() => (LEVELS[this.level()] ? this.level() : 'unknown'));
  protected readonly info = computed(() => LEVELS[this.key()]);
}
