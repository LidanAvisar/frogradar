import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

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
