import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { briefFixture } from '../../testing/fixtures';
import { RadarTeaserComponent } from './radar-teaser.component';

describe('RadarTeaserComponent', () => {
  function render(items = briefFixture().radar ?? []) {
    TestBed.configureTestingModule({ imports: [RadarTeaserComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(RadarTeaserComponent);
    fixture.componentRef.setInput('items', items);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('lists candidates with score, sources count and the profile line', () => {
    const el = render();
    const rows = el.querySelectorAll('li');
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector('.name')?.textContent?.trim()).toBe('Regshield');
    expect(rows[0].querySelector('.score')?.textContent?.trim()).toBe('62');
    expect(rows[0].querySelector('.line')?.textContent).toContain('registry firewall');
    expect(rows[0].querySelector('.meta')?.textContent?.trim()).toBe('3 sources');
    expect(rows[0].querySelector('.badge')).toBeNull();
    expect(rows[1].querySelector('.badge')?.textContent?.trim()).toBe('Watching');
    expect(rows[1].querySelector('.line')).toBeNull();
    expect(rows[1].querySelector('.meta')?.textContent?.trim()).toBe('1 source');
  });

  it('links to the radar page', () => {
    const el = render();
    expect(el.querySelector('a.view')?.getAttribute('href')).toBe('/radar');
    expect(el.querySelector('li a.name')?.getAttribute('href')).toBe('/radar?id=11');
  });

  it('shows an empty state', () => {
    const el = render([]);
    expect(el.textContent).toContain('No new players were discovered yet.');
  });
});
