import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Simple stroke icons (decorative: hidden from screen readers). */
const PATHS: Record<string, string> = {
  brief: 'M4 5h16M4 10h16M4 15h10M4 20h7',
  signals: 'M3 12h4l3-8 4 16 3-8h4',
  battlecards: 'M4 4h10v16H4zM14 7l6 2-4 12-2-0.7',
  ask: 'M4 5h16v11H9l-5 4z',
  missions: 'M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5M12 11a1 1 0 1 0 1 1M12 12l8-8',
  review: 'M4 12l5 5L20 6',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  radar: 'M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5M12 12l7-7',
  alert: 'M12 4l9 16H3zM12 10v4M12 17v.5',
  inbox: 'M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5',
  chevron: 'M6 9l6 6 6-6',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6L6 18',
};

@Component({
  selector: 'fr-icon',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.width]="size()" [attr.height]="size()" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
         aria-hidden="true" focusable="false">
      <path [attr.d]="path()" />
    </svg>
  `,
  styles: [':host { display: inline-flex; flex: none; }'],
})
export class IconComponent {
  readonly name = input.required<string>();
  readonly size = input(18);
  protected readonly path = computed(() => PATHS[this.name()] ?? '');
}
