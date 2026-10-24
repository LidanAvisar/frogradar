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
    evidence: { signals_about: 13, required: 5, lookback_days: 180, enough: true },
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
      { id: 11, date: '2026-10-21T00:00:00Z', title: 'Docs describe reachability', category: 'feature_update', impact: 4, url: 'https://x.test/11', research: true },
      { id: 9, date: '2026-10-20T00:00:00Z', title: 'Minor post', category: 'other', impact: 2, url: 'https://x.test/9', research: false },
      { id: 2, date: '2026-10-14T00:00:00Z', title: 'Firewall in Pro+', category: 'pricing_change', impact: 4, url: 'https://x.test/2', research: false },
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
      { slug: 'sonatype', name: 'Sonatype', relationship: 'competitor', signal_count: 13, generated_at: '2026-10-21T08:00:00Z',
        evidence: { signals_about: 13, required: 5, lookback_days: 180, enough: true } },
      { slug: 'snyk', name: 'Snyk', relationship: 'competitor', signal_count: 0, generated_at: null,
        evidence: { signals_about: 0, required: 5, lookback_days: 180, enough: false } },
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
    // Impact 4, not the newer impact-2 post, and not the newer research finding (D82).
    expect(tiles[1].querySelector('a')?.textContent).toBe('Firewall in Pro+');
    expect(el.querySelector('.timeline li')?.textContent).toContain('Research finding');
    expect(tiles[2].textContent).toContain('13 signals tracked');
    // Citation numbers continue in reading order: positioning [1], opportunities [3] → 2, strengths [2, 1] → 3, 1.
    expect(Array.from(el.querySelectorAll('.list-card sup a')).map((a) => a.textContent)).toEqual(['2', '3', '1']);
    expect(el.querySelector('.thin-label')?.textContent).toContain('Not enough evidence yet');
    expect(el.querySelector('.no-card')?.textContent?.trim()).toBe('Snyk');
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
    const investigate = folded[0].querySelector<HTMLAnchorElement>('a.investigate')!;
    expect(investigate.textContent?.trim()).toBe('Investigate');
    expect(investigate.getAttribute('href')).toContain('/missions?entity_slug=sonatype&capability_slug=');
  });

  it('puts the JFrog opportunities above a full-width matrix, then strengths / weaknesses and the timeline', async () => {
    const el = await render(card());
    const order = Array.from(el.querySelectorAll('h2[id]')).map((h) => h.id);
    expect(order).toEqual(['opp-heading', 'blind-heading', 'matrix-heading', 'str-heading', 'weak-heading', 'timeline-heading']);
    expect(el.querySelector('aside.side')).toBeNull();
    expect(el.querySelector('.for-jfrog')?.getAttribute('aria-label')).toBe('For JFrog');
  });

  it('shows full notes and evidence titles in the matrix, never sliced', async () => {
    const longTitle = 'Sonatype Repository Firewall now blocks malicious packages in Nexus Repository Pro and Self-Hosted editions';
    const el = await render(card({
      matrix: [{
        capability: { slug: 'oci-registry', name: 'OCI / Docker registry', segment: 'artifact_management' },
        jfrog: { level: 'full', note: 'Artifactory docs: OCI repositories (plus Docker repositories). Second sentence.', source: null },
        competitor: { level: 'partial', note: 'Nexus Repository Self-Hosted supports Docker (proxy, hosted and group repositories).',
                      evidence: { id: 2, title: longTitle, url: 'https://x.test/2', published_at: '2026-10-14T00:00:00Z' }, last_verified_at: null },
        changed_recently: false,
      }],
    }));
    const row = el.querySelector('fr-capability-matrix tbody tr')!;
    const notes = Array.from(row.querySelectorAll('fr-clamp-text')).map((n) => n.textContent?.replace(/\s+/g, ' ').trim());
    expect(notes[0]).toContain('Artifactory docs: OCI repositories (plus Docker repositories).');
    expect(notes[0]).not.toContain('Second sentence');
    expect(notes[1]).toContain('Nexus Repository Self-Hosted supports Docker (proxy, hosted and group repositories).');
    const ev = row.querySelector<HTMLAnchorElement>('.evidence fr-clamp-text a.text')!;
    expect(ev.textContent?.trim()).toBe(longTitle); // full title, clamped to 2 lines by CSS
    expect(ev.getAttribute('href')).toBe('https://x.test/2');
    expect(ev.style.getPropertyValue('--fr-clamp-lines')).toBe('2');
    expect(row.querySelector('.evidence .ev-date')?.textContent?.trim()).toBe('14 Oct 2026');
    expect(row.textContent).not.toContain('…');
    expect(row.querySelector('fr-clamp-text .text')?.getAttribute('title')).toContain('Artifactory docs: OCI repositories');
  });

  const noCard = { report_id: null, generated_at: null, positioning: null, strengths: [], opportunities_for_jfrog: [], citations: [] };

  it('explains a competitor without enough evidence for a card and what would fill it', async () => {
    const el = await render(card({ ...noCard, evidence: { signals_about: 1, required: 5, lookback_days: 180, enough: false } }));
    const panel = el.querySelector('.no-evidence')!;
    expect(panel.querySelector('h2')?.textContent?.trim()).toBe('Not enough evidence on Sonatype yet');
    expect(panel.textContent).toContain('A card is generated once signals build up: 1 of 5 signals where Sonatype is the main subject (last 180 days).');
    expect(panel.textContent).toContain('What would fill it');
    expect(panel.textContent).toContain('config/sources.yaml');
    expect(panel.querySelector('a')?.getAttribute('href')).toBe('/missions?entity_slug=sonatype');
    expect(el.querySelector('#opp-heading')).toBeNull();
    expect(el.querySelector('fr-capability-matrix')).not.toBeNull();
  });

  it('says the next run builds the card when the evidence is there but no card exists yet', async () => {
    const el = await render(card({ ...noCard, evidence: { signals_about: 6, required: 5, lookback_days: 180, enough: true } }));
    expect(el.querySelector('.no-evidence')).toBeNull();
    expect(el.textContent).toContain('No battlecard for Sonatype yet');
    expect(el.textContent).toContain('the next daily pipeline run generates the card');
  });
});
