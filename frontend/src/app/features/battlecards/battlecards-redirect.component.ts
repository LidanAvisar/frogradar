import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';

/** /battlecards → the top competitor's card (first with a card, by signal count). */
@Component({
  selector: 'fr-battlecards-redirect',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="fr-page">
      <h1 class="fr-page-title">Battlecards</h1>
      <div class="fr-panel">
        @switch (status()) {
          @case ('loading') { <fr-skeleton aria-label="Loading battlecards" /> }
          @case ('error') { <fr-error-state (retry)="load()" /> }
          @default { <fr-empty-state title="No competitors tracked yet" message="Competitors come from config/competitors.yaml or the Radar." /> }
        }
      </div>
    </div>
  `,
})
export class BattlecardsRedirectComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly status = signal<'loading' | 'error' | 'empty'>('loading');

  constructor() {
    this.load();
  }

  load(): void {
    this.status.set('loading');
    this.api.battlecards().subscribe({
      next: (list) => {
        const top = list.find((c) => c.generated_at) ?? list[0];
        if (top) this.router.navigate(['/battlecards', top.slug], { replaceUrl: true });
        else this.status.set('empty');
      },
      error: () => this.status.set('error'),
    });
  }
}
