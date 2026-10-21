import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import type { Battlecard } from '../../api/models';
import { BattlecardPageComponent } from './battlecard-page.component';

const ref = (id: number) => ({ id, title: `Signal ${id}`, url: `https://x.test/${id}`, published_at: null });

function card(overrides: Partial<Battlecard> = {}): Battlecard {
  return {
    entity: { slug: 'sonatype', name: 'Sonatype', relationship: 'competitor', segments: ['supply_chain_security'] },
    report_id: 7,
    generated_at: '2026-10-21T08:00:00Z',
    model: 'm',
    positioning: { text: 'A safe registry.', signal_ids: [1] },
    strengths: [{ text: 'Firewall.', signal_ids: [2, 1] }],
    weaknesses: [],
    opportunities_for_jfrog: [{ text: 'Lead with cost.', signal_ids: [3] }],
    blind_spots_for_jfrog: [],
    citations: [ref(1), ref(2), ref(3)],
    matrix: [
      {
        capability: { slug: 'package-curation', name: 'Package curation', segment: 'supply_chain_security' },
        jfrog: { level: 'full', note: 'Curation docs: blocks risky packages.', source: null },
        competitor: { level: 'partial', note: 'Firewall in Pro+.', evidence: ref(2), last_verified_at: null },
        changed_recently: true,
      },
      {
        capability: { slug: 'sbom', name: 'SBOM', segment: 'supply_chain_security' },
        jfrog: { level: 'full', note: 'Xray SBOM.', source: null },
        competitor: { level: 'unknown', note: null, evidence: null, last_verified_at: null },
        changed_recently: false,
      },
    ],
    timeline: [
      { id: 9, date: '2026-10-20T00:00:00Z', title: 'Minor post', category: 'other', impact: 2, url: 'https://x.test/9' },
      { id: 2, date: '2026-10-14T00:00:00Z', title: 'Firewall in Pro+', category: 'pricing_change', impact: 4, url: 'https://x.test/2' },
    ],
    coverage: { signals: 13, high_impact: 5, sources: 2, capabilities_known: 1, capabilities_total: 2, first_signal_at: null, last_signal_at: null },
    ...overrides,
  };
}

describe('BattlecardPageComponent', () => {
  async function render(c: Battlecard) {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: 'battlecards/:slug', component: BattlecardPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    const http = TestBed.inject(HttpTestingController);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/battlecards/sonatype', BattlecardPageComponent);
    http.expectOne('/api/battlecards').flush([
      { slug: 'sonatype', name: 'Sonatype', relationship: 'competitor', signal_count: 13, generated_at: '2026-10-21T08:00:00Z' },
      { slug: 'snyk', name: 'Snyk', relationship: 'competitor', signal_count: 0, generated_at: null },
    ]);
    http.expectOne('/api/entities/sonatype/battlecard').flush(c);
    harness.detectChanges();
    return harness.routeNativeElement as HTMLElement;
  }

  it('renders header, tiles with the newest high-impact move, and numbered citations', async () => {
    const el = await render(card());
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('Sonatype');
    expect(el.textContent).toContain('Direct competitor');
    expect(el.textContent).toContain('Supply-chain security');
    const tiles = el.querySelectorAll('.tile');
    expect(tiles[0].querySelector('sup a')?.textContent).toBe('1');
    expect(tiles[1].querySelector('a')?.textContent).toBe('Firewall in Pro+'); // impact 4, not the newer impact-2 post
    expect(tiles[2].textContent).toContain('13 signals tracked');
    // Citation numbers continue in reading order: positioning [1], opportunities [3] → 2, strengths [2, 1] → 3, 1.
    expect(Array.from(el.querySelectorAll('.list-card sup a')).map((a) => a.textContent)).toEqual(['2', '3', '1']);
    expect(el.querySelector('.no-card')?.textContent).toContain('no card yet');
  });

  it('shows levels as symbol + text, evidence links and Investigate for unknown cells', async () => {
    const el = await render(card());
    const matrix = el.querySelector('fr-capability-matrix')!;
    // Shown by default: the capability with an evidenced competitor level.
    const shown = matrix.querySelectorAll(':scope > .scroll tbody tr');
    expect(shown.length).toBe(1);
    expect(shown[0].classList).toContain('changed');
    expect(shown[0].textContent).toContain('Changed recently');
    expect(shown[0].querySelectorAll('fr-level-badge')[1].textContent?.trim()).toBe('◐ Partial');
    expect(shown[0].querySelector('.evidence a')?.getAttribute('href')).toBe('https://x.test/2');
    expect(matrix.querySelector(':scope > .scroll')?.getAttribute('tabindex')).toBe('0');
    // Folded: unknown competitor level, in a closed disclosure, with JFrog's level and Investigate.
    const rest = matrix.querySelector<HTMLDetailsElement>('details.rest')!;
    expect(rest.open).toBeFalse();
    expect(rest.querySelector('summary')?.textContent).toContain('1 capability without evidence yet');
    const folded = rest.querySelectorAll('tbody tr');
    expect(folded.length).toBe(1);
    expect(folded[0].querySelector('th')?.textContent?.trim()).toBe('SBOM');
    expect(folded[0].querySelectorAll('fr-level-badge')[0].textContent?.trim()).toBe('● Full');
    const investigate = folded[0].querySelector('.investigate')!;
    expect(investigate.getAttribute('aria-disabled')).toBe('true');
    expect(investigate.textContent?.replace(/\s+/g, ' ').trim()).toBe('Investigate · Coming soon');
  });

  it('shows an empty state for a competitor without a card', async () => {
    const el = await render(card({ report_id: null, generated_at: null, positioning: null, strengths: [], opportunities_for_jfrog: [], citations: [] }));
    expect(el.textContent).toContain('No battlecard for Sonatype yet');
    expect(el.querySelector('#opp-heading')).toBeNull();
    expect(el.querySelector('fr-capability-matrix')).not.toBeNull();
  });
});
