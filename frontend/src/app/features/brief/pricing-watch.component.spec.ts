import { ComponentFixture, TestBed } from '@angular/core/testing';

import type { PricingItem } from '../../api/models';
import { signalFixture } from '../../testing/fixtures';
import { PricingWatchComponent } from './pricing-watch.component';

describe('PricingWatchComponent', () => {
  let fixture: ComponentFixture<PricingWatchComponent>;

  it('shows the old and the new price on separate aligned lines, no arrow', () => {
    const item: PricingItem = {
      signal: signalFixture({ kind: 'page_diff', title: 'Sonatype raises Nexus Repository Cloud Pro to $1,950 a year' }),
      changes: [{ label: 'Nexus Repository > Cloud > Pro Edition', before: '$1,620 / year + consumption', after: '$1,950 / year + consumption' }],
      changes_source: 'verified_llm',
    };
    fixture = TestBed.createComponent(PricingWatchComponent);
    fixture.componentRef.setInput('items', [item]);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;

    expect(el.querySelector('.headline')?.textContent).toContain('Sonatype raises Nexus Repository Cloud Pro');
    expect(el.querySelector('.label')?.textContent).toContain('Cloud > Pro Edition');
    const dl = el.querySelector('dl.prices')!;
    expect(Array.from(dl.querySelectorAll('dt')).map((d) => d.textContent?.trim())).toEqual(['Was', 'Now']);
    expect(dl.querySelector('dd s')?.textContent).toBe('$1,620 / year + consumption');
    expect(dl.querySelector('dd strong')?.textContent).toBe('$1,950 / year + consumption');
    expect(el.textContent).not.toContain('→');
  });
});
