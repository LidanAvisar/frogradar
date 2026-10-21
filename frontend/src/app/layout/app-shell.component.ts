import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import type { NavCounts } from '../api/models';
import { AskRecentService } from '../core/ask-recent.service';
import { NavCountsService } from '../core/nav-counts.service';
import { PreferencesService } from '../core/preferences.service';
import { IconComponent } from '../shared/icon.component';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  count?: keyof NavCounts;
  countLabel?: string;
}

const NAV: NavItem[] = [
  { path: '/', label: 'Morning Brief', icon: 'brief' },
  { path: '/signals', label: 'Signals', icon: 'signals', count: 'signals', countLabel: 'signals' },
  { path: '/battlecards', label: 'Battlecards', icon: 'battlecards' },
  { path: '/ask', label: 'Ask', icon: 'ask' },
  { path: '/missions', label: 'Missions', icon: 'missions' },
  { path: '/radar', label: 'Radar', icon: 'radar', count: 'radar_new', countLabel: 'new candidates' },
  { path: '/review', label: 'Review queue', icon: 'review', count: 'review_queue', countLabel: 'waiting for review' },
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
  private readonly nav = inject(NavCountsService);
  protected readonly recent = inject(AskRecentService).items;
  protected readonly onAsk = signal(false);
  protected readonly currentAsk = signal<number | null>(null);
  private readonly counts = this.nav.counts;
  private readonly prefs = inject(PreferencesService);
  protected readonly analystName = this.prefs.analystName;

  protected readonly menuOpen = signal(false);
  private readonly menuButton = viewChild<ElementRef<HTMLButtonElement>>('menuButton');

  constructor() {
    this.nav.refresh();
    inject(Router)
      .events.pipe(
        filter((e) => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe((e) => {
        this.menuOpen.set(false);
        const url = (e as NavigationEnd).urlAfterRedirects;
        this.onAsk.set(url.startsWith('/ask'));
        this.currentAsk.set(Number(new URLSearchParams(url.split('?')[1] ?? '').get('c')) || null);
      });
  }

  setName(name: string): void {
    this.prefs.setAnalystName(name);
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
