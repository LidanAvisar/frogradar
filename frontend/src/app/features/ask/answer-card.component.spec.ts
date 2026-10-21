import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { askFixture } from '../../testing/fixtures';
import { AnswerCardComponent } from './answer-card.component';

describe('AnswerCardComponent', () => {
  function render(answer = askFixture()) {
    TestBed.configureTestingModule({ imports: [AnswerCardComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(AnswerCardComponent);
    fixture.componentRef.setInput('answer', answer);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('shows numbered points whose chips point at the matching Sources entries', () => {
    const { el } = render();
    expect(el.querySelector('.head')?.textContent).toContain('Answered from 3 documents · every point cited');
    const points = el.querySelectorAll('.points li');
    expect(points.length).toBe(2);
    expect(points[0].querySelector('strong')?.textContent).toBe('GitHub firewall preview.');
    const chips = Array.from(points[1].querySelectorAll('a.chip'));
    expect(chips.map((c) => c.textContent?.trim())).toEqual(['2', '1']);
    expect(chips[0].getAttribute('href')).toBe('#src-21-2');
    expect(chips[0].getAttribute('aria-label')).toBe('Source 2: Trust What Ships');
    expect(el.querySelector('.meaning')?.textContent).toContain('What it means for JFrog');
    expect(el.querySelector('.meaning a.chip')?.getAttribute('href')).toBe('#src-21-3');
  });

  it('shows the filters used and the mission link for this turn', () => {
    const { el } = render(askFixture({ filters: { company: 'Sonatype', company_slug: 'sonatype', window: 'last 30 days' } }));
    expect(el.querySelector('.filters')?.textContent).toContain('Sonatype · last 30 days');
    const mission = el.querySelector('.mission')!;
    expect(mission.getAttribute('href')).toMatch(/^\/missions\?turn=\d+$/);
    expect(mission.textContent?.trim()).toBe('Dig deeper with a mission');
  });

  it('says plainly when there is not enough evidence', () => {
    const { el } = render(
      askFixture({
        not_enough_evidence: true, points: [], meaning_for_jfrog: null, sources: [],
        gap: 'Nothing about GitLab pricing was collected.', suggestions: ['Track GitLab’s pricing page.'],
      }),
    );
    expect(el.querySelector('h3')?.textContent).toContain('Not enough evidence in FrogRadar');
    expect(el.textContent).toContain('Nothing about GitLab pricing was collected.');
    expect(el.querySelector('.suggestions li')?.textContent).toContain('Track GitLab’s pricing page.');
    expect(el.querySelector('.points')).toBeNull();
    expect(el.querySelector('.mission')?.textContent?.trim()).toBe('Send a research mission');
  });

  it('emits citation clicks and ratings, and reflects the chosen rating', () => {
    const { fixture, el } = render();
    const cited: number[] = [];
    const rated: string[] = [];
    fixture.componentInstance.cite.subscribe((n) => cited.push(n));
    fixture.componentInstance.rate.subscribe((r) => rated.push(r));
    (el.querySelector('a.chip') as HTMLAnchorElement).click();
    const [helpful, wrong] = Array.from(el.querySelectorAll<HTMLButtonElement>('.actions button'));
    helpful.click();
    wrong.click();
    expect(cited).toEqual([1]); // default navigation prevented (no reload)
    expect(rated).toEqual(['helpful', 'wrong']);
    fixture.componentRef.setInput('feedback', 'helpful');
    fixture.detectChanges();
    expect(helpful.getAttribute('aria-pressed')).toBe('true');
    expect(wrong.getAttribute('aria-pressed')).toBe('false');
  });
});
