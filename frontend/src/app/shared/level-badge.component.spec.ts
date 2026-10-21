import { TestBed } from '@angular/core/testing';

import { LevelBadgeComponent } from './level-badge.component';

describe('LevelBadgeComponent', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [LevelBadgeComponent] }));

  function text(level: string): { sym: string; all: string; hidden: string | null } {
    const fixture = TestBed.createComponent(LevelBadgeComponent);
    fixture.componentRef.setInput('level', level);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const sym = el.querySelector('.sym')!;
    return { sym: sym.textContent!, all: el.textContent!.trim(), hidden: sym.getAttribute('aria-hidden') };
  }

  it('shows a symbol and a word for every level', () => {
    expect(text('full').all).toBe('● Full');
    expect(text('partial').all).toBe('◐ Partial');
    expect(text('planned').all).toBe('◔ Planned');
    expect(text('none').all).toBe('✕ None');
    expect(text('unknown').all).toBe('○ Unknown');
  });

  it('hides the symbol from screen readers and treats unknown values as unknown', () => {
    const t = text('bogus');
    expect(t.all).toBe('○ Unknown');
    expect(t.hidden).toBe('true');
  });
});
