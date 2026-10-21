import { ComponentFixture, TestBed } from '@angular/core/testing';

import type { Evidence } from '../api/models';
import { EvidenceQuoteComponent } from './evidence-quote.component';

describe('EvidenceQuoteComponent', () => {
  let fixture: ComponentFixture<EvidenceQuoteComponent>;

  function render(evidence: Evidence | null, sourceName = 'Sonatype Blog'): HTMLElement {
    fixture.componentRef.setInput('evidence', evidence);
    fixture.componentRef.setInput('sourceName', sourceName);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  beforeEach(() => {
    fixture = TestBed.createComponent(EvidenceQuoteComponent);
  });

  it('shows a verified quote verbatim with its source', () => {
    const el = render({ quote: 'Blocks malicious packages.', verified: true, side: 'after', section: null });
    expect(el.querySelector('blockquote')?.textContent).toContain('“Blocks malicious packages.”');
    expect(el.textContent).toContain('Verbatim from Sonatype Blog');
  });

  it('shows a removed quote as "Removed: …" with the section', () => {
    const el = render({ quote: 'Team discussions', verified: true, side: 'before', section: 'Plans > Team' });
    expect(el.querySelector('blockquote')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('Removed: ‘Team discussions’');
    expect(el.textContent).toContain('Plans > Team');
  });

  it('renders nothing for an unverified quote', () => {
    const el = render({ quote: 'Not in the source', verified: false, side: 'after', section: null });
    expect(el.querySelector('blockquote')).toBeNull();
    expect(el.textContent?.trim()).toBe('');
  });
});
