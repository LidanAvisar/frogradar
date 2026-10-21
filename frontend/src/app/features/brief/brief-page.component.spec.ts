import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { briefFixture } from '../../testing/fixtures';
import { BriefPageComponent } from './brief-page.component';

describe('BriefPageComponent', () => {
  let fixture: ComponentFixture<BriefPageComponent>;
  let http: HttpTestingController;
  let el: HTMLElement;

  beforeEach(() => {
    localStorage.removeItem('frogradar.executiveView');
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(BriefPageComponent);
    el = fixture.nativeElement;
    fixture.detectChanges();
  });

  afterEach(() => localStorage.removeItem('frogradar.executiveView'));

  function respond(brief = briefFixture()): void {
    http.expectOne((r) => r.url === '/api/brief').flush(brief);
    fixture.detectChanges();
  }

  it('shows skeletons while loading, then the ranked cards', () => {
    expect(el.querySelector('[aria-busy="true"]')).not.toBeNull();
    respond();
    const cards = el.querySelectorAll('fr-signal-card');
    expect(cards.length).toBe(2);
    expect(el.querySelector('h1')?.textContent).toContain('Your morning brief');
    expect(el.textContent).toContain('WEDNESDAY · 21 OCTOBER 2026');
    expect(el.textContent).toContain('Vendor claim');
    expect(el.textContent).toContain('30 items filtered as not relevant');
    expect(el.textContent).toContain('3 new since yesterday · 2 high impact');
  });

  it('cards start collapsed; Expand all / Collapse all toggles every card', () => {
    respond();
    const details = () => Array.from(el.querySelectorAll<HTMLElement>('.details'));
    expect(details().every((d) => d.hidden)).toBeTrue();

    const all = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Expand all'))!;
    all.click();
    fixture.detectChanges();
    expect(details().every((d) => !d.hidden)).toBeTrue();
    expect(all.textContent).toContain('Collapse all');

    all.click();
    fixture.detectChanges();
    expect(details().every((d) => d.hidden)).toBeTrue();
  });

  it('remembers expanded cards in memory for the session', () => {
    respond();
    (el.querySelector('.chevron') as HTMLButtonElement).click();
    fixture.detectChanges();

    // Leave and come back (new component, same app session).
    fixture.destroy();
    fixture = TestBed.createComponent(BriefPageComponent);
    el = fixture.nativeElement;
    fixture.detectChanges();
    respond();
    const details = el.querySelectorAll<HTMLElement>('.details');
    expect(details[0].hidden).toBeFalse();
    expect(details[1].hidden).toBeTrue();
    expect(Object.keys(localStorage).some((k) => k.includes('expand'))).toBeFalse();
  });

  it('uses the daily brief headline when there is one', () => {
    respond(briefFixture({ headline: 'Sonatype reprices Nexus', overview: 'Two moves matter today.' }));
    expect(el.querySelector('h1')?.textContent).toContain('Sonatype reprices Nexus');
    expect(el.textContent).toContain('Two moves matter today.');
  });

  it('switches to one-line cards and remembers it', () => {
    respond();
    const toggle = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Executive view'))!;
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelectorAll('article.exec').length).toBe(2);
    expect(el.querySelector('.why')).toBeNull();
    expect(el.querySelector('.main')!.querySelector('fr-impact-badge, fr-confidence-badge, blockquote')).toBeNull();
    expect(el.textContent).not.toContain('Expand all');
    expect(localStorage.getItem('frogradar.executiveView')).toBe('true');
  });

  it('disabled actions say "Coming soon" and Investigate links to Missions', () => {
    respond();
    const approve = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Approve')!;
    expect(approve.getAttribute('aria-disabled')).toBe('true');
    expect(approve.getAttribute('data-hint')).toBe('Coming soon');
    const investigate = el.querySelector('a[href^="/missions"]');
    expect(investigate?.getAttribute('href')).toBe('/missions?signal=1');
  });

  it('shows an error with a retry', () => {
    http.expectOne('/api/brief?days=7&limit=5').flush('down', { status: 503, statusText: 'Down' });
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')).not.toBeNull();
    (el.querySelector('[role="alert"] button') as HTMLButtonElement).click();
    respond();
    expect(el.querySelectorAll('fr-signal-card').length).toBe(2);
  });
});
