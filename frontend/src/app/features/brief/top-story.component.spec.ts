import { ComponentFixture, TestBed } from '@angular/core/testing';

import { storyFixture } from '../../testing/fixtures';
import { TopStoryComponent } from './top-story.component';

describe('TopStoryComponent', () => {
  let fixture: ComponentFixture<TopStoryComponent>;
  let el: HTMLElement;

  function render(overrides = {}): void {
    fixture = TestBed.createComponent(TopStoryComponent);
    fixture.componentRef.setInput('story', storyFixture(overrides));
    fixture.detectChanges();
    el = fixture.nativeElement;
  }

  it('shows the eyebrow, plain title and the three labelled rows with numbered sources', () => {
    render();
    expect(el.querySelector('.label')?.textContent).toContain('Top story today');
    expect(Array.from(el.querySelectorAll('dt')).map((d) => d.textContent?.trim())).toEqual([
      'What happened',
      'Why it matters',
      'Suggested move',
    ]);
    const nums = (sel: string) =>
      Array.from(el.querySelectorAll<HTMLAnchorElement>(sel)).map((a) => a.textContent?.trim());
    expect(nums('.title .cite a')).toEqual(['1']);
    expect(nums('dd .cite a')).toEqual(['1', '2', '1', '2']); // ids 7, 9 / 7 / 9
    const link = el.querySelector<HTMLAnchorElement>('dd .cite a')!;
    expect(link.getAttribute('href')).toBe('https://s.test/7');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('shows company, affected product, confidence and the source link', () => {
    render();
    expect(el.querySelector('fr-company-chip')?.textContent).toContain('GitHub');
    expect(el.querySelector('.affects')?.textContent).toContain('JFrog Curation');
    expect(el.querySelector('fr-confidence-badge')).not.toBeNull();
    expect(el.querySelector('fr-source-link a')?.getAttribute('href')).toBe('https://github.blog/changelog/1');
  });

  it('fallback story has no suggested move and says it is the top-ranked signal', () => {
    render({ source: 'top_signal', suggested_move: null, company: null, affects: [] });
    expect(Array.from(el.querySelectorAll('dt')).map((d) => d.textContent?.trim())).toEqual([
      'What happened',
      'Why it matters',
    ]);
    expect(el.textContent).toContain('Highest-ranked signal · no daily brief yet');
    expect(el.querySelector('fr-company-chip')).toBeNull();
  });
});
