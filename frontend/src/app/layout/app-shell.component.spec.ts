import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { AskRecentService } from '../core/ask-recent.service';
import { StatusService } from '../core/status.service';
import { statusFixture } from '../testing/status-fixture';
import { AppShellComponent } from './app-shell.component';

@Component({ standalone: true, template: '' })
class BlankComponent {}

describe('AppShellComponent menu', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: '', component: BlankComponent }, { path: 'signals', component: BlankComponent }]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
  });

  it('opens and closes the nav with aria-expanded, Escape and navigation', async () => {
    const fixture = TestBed.createComponent(AppShellComponent);
    const el: HTMLElement = fixture.nativeElement;
    document.body.appendChild(el); // focus checks need the element in the document
    fixture.detectChanges();
    const button = el.querySelector<HTMLButtonElement>('.menu-button')!;
    const nav = el.querySelector<HTMLElement>('#main-nav')!;
    // The button only shows at phone width; the test browser is wider, and hidden buttons can't take focus.
    button.style.display = 'inline-flex';
    expect(button.getAttribute('aria-controls')).toBe('main-nav');
    expect(button.getAttribute('aria-expanded')).toBe('false');

    button.click();
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(nav.classList).toContain('open');

    nav.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);

    button.click();
    fixture.detectChanges();
    await TestBed.inject(Router).navigateByUrl('/signals');
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('false');
    el.remove();
  });
});

describe('AppShellComponent recent questions', () => {
  it('lists recent Ask conversations on /ask only', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: '', component: BlankComponent }, { path: 'ask', component: BlankComponent }]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    TestBed.inject(AskRecentService).items.set([
      { id: 3, title: 'Who launched a package firewall?', turns: 2, updated_at: '2026-10-21T08:00:00Z' },
    ]);
    const fixture = TestBed.createComponent(AppShellComponent);
    const el: HTMLElement = fixture.nativeElement;
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    fixture.detectChanges();
    expect(el.querySelector('.recent')).toBeNull();
    await router.navigateByUrl('/ask');
    fixture.detectChanges();
    const link = el.querySelector<HTMLAnchorElement>('.recent-link')!;
    expect(link.textContent?.trim()).toBe('Who launched a package firewall?');
    expect(link.getAttribute('href')).toBe('/ask?c=3');
    expect(link.getAttribute('aria-current')).toBeNull();
    await router.navigateByUrl('/ask?c=3');
    fixture.detectChanges();
    expect(link.getAttribute('aria-current')).toBe('page');
  });

});

describe('AppShellComponent status', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: '', component: BlankComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  it("shows today's spend and turns the status dot amber while a core feature is limited", () => {
    const fixture = TestBed.createComponent(AppShellComponent);
    const el: HTMLElement = fixture.nativeElement;
    const status = TestBed.inject(StatusService);
    status.status.set(statusFixture()); // ask off
    fixture.detectChanges();
    expect(el.querySelector('.spend')?.textContent).toContain('$0.03 of $2.00');
    expect(el.querySelector('.dot.limited')).not.toBeNull();
    expect(el.textContent).not.toContain('Demo');

    status.status.set(statusFixture({ features: [{ name: 'ask', label: 'Free-form Ask', state: 'enabled', reason: '' }] }));
    fixture.detectChanges();
    expect(el.querySelector('.dot.limited')).toBeNull();
  });
});
