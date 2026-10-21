import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { candidateFixture } from '../../testing/fixtures';
import { RadarDetailPageComponent } from './radar-detail-page.component';

describe('RadarDetailPageComponent', () => {
  it('labels partly verified statements with the verifier note and links Investigate to a mission', async () => {
    TestBed.configureTestingModule({
      providers: [provideNoopAnimations(), provideRouter([{ path: 'radar/:id', component: RadarDetailPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    const http = TestBed.inject(HttpTestingController);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/radar/11', RadarDetailPageComponent);
    http.expectOne('/api/radar/11').flush(candidateFixture());
    harness.detectChanges();
    const el = harness.routeNativeElement as HTMLElement;
    const rows = el.querySelectorAll('.statement');
    expect(rows[0].textContent).not.toContain('Partly verified');
    expect(rows[1].textContent).toContain('Partly verified');
    expect(rows[1].textContent).toContain('Not confirmed: The week is not stated.');
    const investigate = Array.from(el.querySelectorAll<HTMLAnchorElement>('a.fr-button')).find((a) => a.textContent?.trim() === 'Investigate')!;
    expect(investigate.getAttribute('href')).toBe('/missions?candidate=11');
  });
});
