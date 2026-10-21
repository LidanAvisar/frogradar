import { TestBed } from '@angular/core/testing';

import { candidateFixture } from '../testing/fixtures';
import { scoreParts } from './score';
import { ScoreBarComponent } from './score-bar.component';

describe('ScoreBarComponent', () => {
  function render(detailed = false, breakdown = candidateFixture().breakdown) {
    TestBed.configureTestingModule({ imports: [ScoreBarComponent] });
    const fixture = TestBed.createComponent(ScoreBarComponent);
    fixture.componentRef.setInput('score', 61.5);
    fixture.componentRef.setInput('breakdown', breakdown);
    fixture.componentRef.setInput('detailed', detailed);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the score and a decorative bar with one segment per component', () => {
    const el = render();
    expect(el.querySelector('.score')?.textContent?.trim()).toBe('62');
    expect(el.querySelector('.score')?.getAttribute('aria-label')).toBe('Emergence score 62 of 100');
    const bar = el.querySelector('.bar')!;
    expect(bar.getAttribute('aria-hidden')).toBe('true');
    expect(bar.querySelectorAll('.seg').length).toBe(6);
  });

  it('gives the breakdown as text in display order', () => {
    const el = render();
    const rows = Array.from(el.querySelectorAll('.parts li')).map(
      (li) => `${li.querySelector('.label')!.textContent} ${li.querySelector('.pts')!.textContent!.replace(/\s+/g, ' ').trim()}`,
    );
    expect(rows).toEqual([
      'Relevance 32.2 of 35',
      'Sources 13.5 of 20',
      'Mentions 4 of 10',
      'Velocity 3.3 of 10',
      'Traction 2 of 10',
      'Recency 6.5 of 15',
    ]);
    expect(el.querySelector('details')?.open).toBeFalse();
  });

  it('explains each component in words when detailed', () => {
    const el = render(true);
    expect(el.querySelector('details')?.open).toBeTrue();
    const why = Array.from(el.querySelectorAll('.why')).map((w) => w.textContent!.trim());
    expect(why[0]).toBe("The profile's JFrog relation: threatens JFrog products; highest mention impact 4 of 5.");
    expect(why[3]).toBe('1 mention in the last 14 days, 0 in the weeks before.');
    expect(why[4]).toBe('800 GitHub stars, about 12.5 per day.');
    expect(why[5]).toBe('Last mentioned 1 day ago.');
  });

  it('renders only the number without a breakdown', () => {
    const el = render(false, null);
    expect(el.querySelector('.bar')).toBeNull();
    expect(el.querySelector('details')).toBeNull();
    expect(scoreParts(null)).toEqual([]);
  });
});
