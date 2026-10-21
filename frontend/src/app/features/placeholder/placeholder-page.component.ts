import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';

import { IconComponent } from '../../shared/icon.component';

/** A screen that is not built yet: its name, "Coming soon" and one line about what it will do. */
@Component({
  selector: 'fr-placeholder-page',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <header>
        <p class="eyebrow">Coming soon</p>
        <h1>{{ data()['heading'] }}</h1>
      </header>
      <div class="card">
        <fr-icon [name]="data()['icon']" [size]="22" />
        <p>{{ data()['description'] }}</p>
      </div>
    </div>
  `,
  styles: [`
    .page {
      max-width: var(--fr-content-max); margin: 0 auto;
      padding: var(--fr-content-pad-y) var(--fr-content-pad-x);
      display: flex; flex-direction: column; gap: var(--fr-gap-lg);
    }
    h1 { font-size: var(--fr-text-h1); line-height: var(--fr-lh-h1); letter-spacing: var(--fr-ls-h1); font-weight: 600; margin-top: 4px; }
    .card {
      display: flex; align-items: flex-start; gap: var(--fr-gap-sm); max-width: 640px;
      padding: var(--fr-card-pad); background: var(--fr-surface);
      border: 1px solid var(--fr-border); border-radius: var(--fr-radius-card); color: var(--fr-ink-2);
    }
    fr-icon { color: var(--fr-accent); margin-top: 1px; }
    @media (max-width: 720px) { .page { padding: var(--fr-gap-lg) var(--fr-content-pad-x-phone); } }
  `],
})
export class PlaceholderPageComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly routeData = toSignal(this.route.data, { initialValue: {} as Record<string, string> });
  protected readonly data = computed(() => this.routeData() as Record<string, string>);
}
