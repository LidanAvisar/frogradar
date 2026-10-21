import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { signalFixture } from '../../testing/fixtures';
import { parseFilters, toApiQuery, toQueryParams } from './signal-filters';
import { SignalsPageComponent } from './signals-page.component';

describe('signal filters', () => {
  it('parses the Brief tile links', () => {
    expect(parseFilters({ since: '24h', min_impact: '4' })).toEqual(
      jasmine.objectContaining({ since: '24h', minImpact: 4, page: 1 }),
    );
    expect(parseFilters({ kind: 'page_diff', days: '7' })).toEqual(jasmine.objectContaining({ kind: 'page_diff', since: '7d' }));
    expect(parseFilters({ min_impact: '9', since: '1y', kind: 'video', page: '-2' })).toEqual(
      jasmine.objectContaining({ minImpact: null, since: null, kind: null, page: 1 }),
    );
  });

  it('round-trips to query params and the API query', () => {
    const f = parseFilters({ q: 'firewall', entity: 'sonatype', since: '7d', page: '2' });
    expect(toQueryParams(f)).toEqual(
      jasmine.objectContaining({ q: 'firewall', entity: 'sonatype', since: '7d', page: 2, days: null, kind: null }),
    );
    expect(toApiQuery(f, 20)).toEqual(jasmine.objectContaining({ q: 'firewall', entity: 'sonatype', window: '7d', page: 2, page_size: 20 }));
  });
});

describe('SignalsPageComponent (URL-synced filters)', () => {
  let http: HttpTestingController;
  let router: Router;

  async function open(url: string) {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: 'signals', component: SignalsPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    const fixture = TestBed.createComponent(SignalsPageComponent);
    fixture.detectChanges();
    http.expectOne('/api/entities').flush([{ id: 2, slug: 'sonatype', name: 'Sonatype', type: 'company', relationship: 'competitor', is_self: false, signal_count: 3, last_signal_at: null }]);
    return fixture;
  }

  it('loads with the URL filters and shows them in the controls', async () => {
    const fixture = await open('/signals?since=24h&min_impact=4');
    const req = http.expectOne((r) => r.url === '/api/signals');
    expect(req.request.params.get('window')).toBe('24h');
    expect(req.request.params.get('min_impact')).toBe('4');
    req.flush({ items: [signalFixture()], total: 1, page: 1, page_size: 20 });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector<HTMLSelectElement>('#signals-window')!.value).toBe('24h');
    expect(el.querySelector<HTMLSelectElement>('#signals-impact')!.value).toBe('4');
    expect(el.querySelector('.results-title')?.textContent?.trim()).toBe('1 signal');
    expect(el.querySelectorAll('fr-signal-card').length).toBe(1);
  });

  it('writes a changed filter to the URL and resets the page', async () => {
    const fixture = await open('/signals?page=3');
    http.expectOne((r) => r.url === '/api/signals').flush({ items: [], total: 0, page: 3, page_size: 20 });
    fixture.detectChanges();
    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>('#signals-kind')!;
    select.value = 'page_diff';
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    expect(router.url).toBe('/signals?kind=page_diff');
    const req = http.expectOne((r) => r.url === '/api/signals');
    expect(req.request.params.get('kind')).toBe('page_diff');
    expect(req.request.params.get('page')).toBe('1');
    req.flush({ items: [], total: 0, page: 1, page_size: 20 });
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No signals match these filters');
  });

  it('maps the old days=7 link to since=7d', async () => {
    await open('/signals?kind=page_diff&days=7');
    const req = http.expectOne((r) => r.url === '/api/signals');
    expect(req.request.params.get('window')).toBe('7d');
    req.flush({ items: [], total: 0, page: 1, page_size: 20 });
  });

  it('paginates', async () => {
    const fixture = await open('/signals');
    http.expectOne((r) => r.url === '/api/signals').flush({ items: [signalFixture()], total: 45, page: 1, page_size: 20 });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.pager')?.textContent).toContain('Page 1 of 3');
    Array.from(el.querySelectorAll<HTMLButtonElement>('.pager button')).find((b) => b.textContent?.includes('Next'))!.click();
    await fixture.whenStable();
    expect(router.url).toBe('/signals?page=2');
    http.expectOne((r) => r.url === '/api/signals' && r.params.get('page') === '2').flush({ items: [], total: 45, page: 2, page_size: 20 });
  });
});
