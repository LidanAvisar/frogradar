import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { catchError, of } from 'rxjs';

import type { NavCounts } from '../api/models';
import { ApiService } from '../core/api.service';
import { IconComponent } from '../shared/icon.component';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  count?: keyof NavCounts;
}

const NAV: NavItem[] = [
  { path: '/', label: 'Morning Brief', icon: 'brief' },
  { path: '/signals', label: 'Signals', icon: 'signals', count: 'signals' },
  { path: '/battlecards', label: 'Battlecards', icon: 'battlecards' },
  { path: '/ask', label: 'Ask', icon: 'ask' },
  { path: '/missions', label: 'Missions', icon: 'missions' },
  { path: '/review', label: 'Review queue', icon: 'review', count: 'review_queue' },
];

/** Sidebar navigation + main content area. Stacks vertically at phone width. */
@Component({
  selector: 'fr-app-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app-shell.component.html',
  styleUrl: './app-shell.component.scss',
})
export class AppShellComponent {
  private readonly counts = toSignal(
    inject(ApiService).nav().pipe(catchError(() => of(null))),
    { initialValue: null },
  );

  protected readonly items = computed(() =>
    NAV.map((item) => ({
      ...item,
      countValue: item.count ? (this.counts()?.[item.count] ?? null) : null,
    })),
  );
}
