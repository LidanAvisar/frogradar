import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { briefFixture, storyFixture } from '../../testing/fixtures';
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

  it('shows skeletons while loading, then stat tiles and the other developments', () => {
    expect(el.querySelector('[aria-busy="true"]')).not.toBeNull();
    respond();
    expect(el.querySelectorAll('fr-signal-card').length).toBe(2);
    expect(el.querySelector('#dev-heading')?.textContent).toContain('Other developments');
    expect(el.textContent).toContain('WEDNESDAY · 21 OCTOBER 2026');
    expect(el.textContent).toContain('Vendor claim');
    expect(el.textContent).toContain('30 items filtered as not relevant');
    expect(el.textContent).not.toContain('What matters today');

    const tiles = Array.from(el.querySelectorAll<HTMLAnchorElement>('a.tile'));
    expect(tiles.map((t) => t.getAttribute('aria-label'))).toEqual([
      '3 new signals, since yesterday',
      '2 high impact today, new, impact 4-5',
      '1 price change, last 7 days',
    ]);
    expect(tiles[0].getAttribute('href')).toBe('/signals?since=24h');
    expect(tiles[1].getAttribute('href')).toBe('/signals?since=24h&min_impact=4');
    expect(tiles[2].getAttribute('href')).toBe('/signals?kind=page_diff&days=7');
    expect(el.querySelector('fr-top-story')).toBeNull(); // none in this fixture
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

  it('leads with the top story card', () => {
    respond(briefFixture({ top_story: storyFixture() }));
    const story = el.querySelector('fr-top-story')!;
    expect(story.textContent).toContain('Top story today');
    expect(story.querySelector('h2')?.textContent).toContain('GitHub adds a package firewall that overlaps with Curation');
    expect(el.querySelector('h1')?.textContent).toContain('Morning Brief');
  });

  it('executive view shows only the stat tiles and the top story, and is remembered', () => {
    respond(briefFixture({ top_story: storyFixture() }));
    const toggle = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Executive view'))!;
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelectorAll('a.tile').length).toBe(3);
    expect(el.querySelector('fr-top-story')).not.toBeNull();
    expect(el.querySelector('fr-signal-card')).toBeNull();
    expect(el.querySelector('.rail')).toBeNull();
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
    http.expectOne('/api/brief?days=7&limit=4').flush('down', { status: 503, statusText: 'Down' });
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')).not.toBeNull();
    (el.querySelector('[role="alert"] button') as HTMLButtonElement).click();
    respond();
    expect(el.querySelectorAll('fr-signal-card').length).toBe(2);
  });
});
