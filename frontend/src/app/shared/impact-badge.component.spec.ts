import { TestBed } from '@angular/core/testing';

import { ImpactBadgeComponent } from './impact-badge.component';

describe('ImpactBadgeComponent', () => {
  for (const [impact, cls] of [[5, 'level-5'], [4, 'level-4'], [3, 'level-3'], [2, 'level-low'], [1, 'level-low']] as const) {
    it(`uses ${cls} for impact ${impact}`, () => {
      const fixture = TestBed.createComponent(ImpactBadgeComponent);
      fixture.componentRef.setInput('impact', impact);
      fixture.detectChanges();
      const pill = (fixture.nativeElement as HTMLElement).querySelector('.pill')!;
      expect(pill.classList).toContain(cls);
      expect(pill.getAttribute('aria-label')).toBe(`Impact ${impact} of 5`);
    });
  }

  it('renders nothing without an impact', () => {
    const fixture = TestBed.createComponent(ImpactBadgeComponent);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('.pill')).toBeNull();
  });
});
