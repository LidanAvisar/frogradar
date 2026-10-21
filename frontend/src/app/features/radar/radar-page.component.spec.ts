import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Router, provideRouter } from '@angular/router';

import { candidateFixture } from '../../testing/fixtures';
import { RadarPageComponent } from './radar-page.component';

describe('RadarPageComponent', () => {
  let http: HttpTestingController;
  let router: Router;

  async function render(url = '/radar') {
    TestBed.configureTestingModule({
      providers: [provideNoopAnimations(), provideRouter([{ path: 'radar', component: RadarPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    const fixture = TestBed.createComponent(RadarPageComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('lists top candidates and folds low scores and "none" into Lower relevance', async () => {
    const fixture = await render();
    const req = http.expectOne((r) => r.url === '/api/radar');
    expect(req.request.params.get('status')).toBe('new,watching');
    req.flush({
      items: [
        candidateFixture(),
        candidateFixture({ id: 12, name: 'Nooverlap', score: 50, relation: 'none', relation_products: [] }),
        candidateFixture({ id: 13, name: 'Tiny', score: 20, relation: null, relation_products: [] }),
      ],
      total: 3,
      lower_relevance_below: 35,
    });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const top = el.querySelectorAll(':scope .list > li fr-radar-row');
    expect(el.querySelector('.results-title')?.textContent?.trim()).toBe('3 candidates');
    expect(el.querySelector('.lower summary')?.textContent).toContain('Lower relevance (2)');
    expect(el.querySelector('.lower')?.textContent).toContain('Nooverlap');
    const first = top[0] as HTMLElement;
    expect(first.querySelector('h3')?.textContent?.trim()).toBe('Regshield');
    expect(first.textContent).toContain('Threatens');
    expect(first.textContent).toContain('JFrog Curation');
    expect(first.textContent).toContain('An open-source registry firewall.');
    expect(first.querySelector('a')?.getAttribute('href')).toBe('/radar/11');
  });

  it('syncs the relation filter to the URL', async () => {
    const fixture = await render('/radar?relation=threatens');
    const req = http.expectOne((r) => r.url === '/api/radar');
    expect(req.request.params.get('relation')).toBe('threatens');
    req.flush({ items: [], total: 0, lower_relevance_below: 35 });
    fixture.detectChanges();
    const both = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll<HTMLLabelElement>('.fr-chip-choice')).find((l) => l.textContent?.trim() === 'Both')!;
    both.querySelector('input')!.click();
    await fixture.whenStable();
    expect(router.url).toBe('/radar?relation=threatens,both');
    http.expectOne((r) => r.url === '/api/radar' && r.params.get('relation') === 'threatens,both').flush({ items: [], total: 0, lower_relevance_below: 35 });
  });
});
