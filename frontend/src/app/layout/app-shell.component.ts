import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { catchError, filter, of } from 'rxjs';

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

/**
 * Sidebar navigation + main content area. At phone width the sidebar becomes a top bar and the
 * nav opens from a Menu button (closes on navigation and on Escape).
 */
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

  protected readonly menuOpen = signal(false);
  private readonly menuButton = viewChild<ElementRef<HTMLButtonElement>>('menuButton');

  constructor() {
    inject(Router)
      .events.pipe(
        filter((e) => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => this.menuOpen.set(false));
  }

  toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  closeMenu(returnFocus = false): void {
    if (!this.menuOpen()) return;
    this.menuOpen.set(false);
    if (returnFocus) this.menuButton()?.nativeElement.focus();
  }

  protected readonly items = computed(() =>
    NAV.map((item) => ({
      ...item,
      countValue: item.count ? (this.counts()?.[item.count] ?? null) : null,
    })),
  );
}
