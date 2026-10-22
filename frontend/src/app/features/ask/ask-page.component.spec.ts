import { ApplicationRef } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed, fakeAsync, flush } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { askFixture } from '../../testing/fixtures';
import { DEMO_QUESTIONS, demoStatusFixture } from '../../testing/status-fixture';
import { StatusService } from '../../core/status.service';
import { AskPageComponent } from './ask-page.component';

describe('AskPageComponent', () => {
  let http: HttpTestingController;

  async function open(url = '/ask') {
    TestBed.configureTestingModule({
      providers: [
        provideNoopAnimations(),
        provideRouter([{ path: 'ask', component: AskPageComponent }]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url, AskPageComponent);
    http.match('/api/ask/conversations?limit=5').forEach((r) => r.flush([]));
    return harness;
  }

  function el(h: RouterTestingHarness): HTMLElement {
    return h.routeNativeElement as HTMLElement;
  }

  function type(h: RouterTestingHarness, text: string): void {
    const input = el(h).querySelector<HTMLInputElement>('#ask-input')!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    el(h).querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    h.detectChanges();
  }

  it('asks, shows a loading state, then the answer, the sources rail and a live announcement', async () => {
    const h = await open();
    expect(el(h).querySelectorAll('.intro .chip-btn').length).toBe(3);
    type(h, 'Who launched a package firewall?');
    expect(el(h).querySelector('[aria-busy=true]')).not.toBeNull();
    expect(el(h).querySelector('[role=status]')?.textContent).toContain('Searching your intelligence');
    const req = http.expectOne('/api/ask');
    expect(req.request.body).toEqual({ question: 'Who launched a package firewall?', conversation_id: null });
    req.flush(askFixture());
    http.match('/api/ask/conversations?limit=5').forEach((r) => r.flush([]));
    await h.fixture.whenStable();
    h.detectChanges();
    expect(el(h).querySelector('.bubble')?.textContent).toContain('Who launched a package firewall?');
    expect(el(h).querySelectorAll('fr-answer-card .points li').length).toBe(2);
    expect(el(h).querySelector('[role=status]')?.textContent).toContain('Answer ready: 2 points from 3 documents.');
    const rail = el(h).querySelectorAll('fr-sources-rail .src');
    expect(rail.length).toBe(3);
    expect(rail[1].id).toBe('src-21-2');
    expect(rail[0].textContent).toContain('1 · official');
    expect(rail[2].querySelector('a')?.getAttribute('href')).toBe('/battlecards/sonatype');
    expect(TestBed.inject(Router).url).toBe('/ask?c=3');
  });

  it('a follow-up chip asks in the same conversation', async () => {
    const h = await open('/ask?c=3');
    http.expectOne('/api/ask/conversations/3').flush({ id: 3, title: 'x', turns: [askFixture()] });
    h.detectChanges();
    const chips = Array.from(el(h).querySelectorAll<HTMLButtonElement>('[aria-label="Suggested follow-up questions"] .chip-btn'));
    expect(chips.map((c) => c.textContent?.trim())).toEqual(['Compare the two firewalls', 'What does Curation cover?', 'Pricing?']);
    chips[0].click();
    const req = http.expectOne('/api/ask');
    expect(req.request.body).toEqual({ question: 'Compare the two firewalls', conversation_id: 3 });
    req.flush(askFixture({ turn_id: 22, position: 2, question: 'Compare the two firewalls', not_enough_evidence: true, points: [], sources: [], meaning_for_jfrog: null, gap: 'No comparison data.' }));
    http.match('/api/ask/conversations?limit=5').forEach((r) => r.flush([]));
    h.detectChanges();
    expect(el(h).querySelectorAll('.turn').length).toBe(2);
    expect(el(h).querySelector('[role=status]')?.textContent).toContain('not enough evidence');
  });

  it('shows the API error (budget) and keeps the question', async () => {
    const h = await open();
    type(h, 'Anything new at GitHub?');
    http.expectOne('/api/ask').flush({ detail: 'LLM budget reached, try again tomorrow' }, { status: 429, statusText: 'Too Many' });
    h.detectChanges();
    expect(el(h).querySelector('[role=alert]')?.textContent).toContain('LLM budget reached');
    expect(el(h).querySelector<HTMLInputElement>('#ask-input')!.value).toBe('Anything new at GitHub?');
    type(h, 'a');
    expect(el(h).querySelector('[role=alert]')?.textContent).toContain('at least 3 characters');
    http.expectNone('/api/ask');
  });

  it('"Something’s wrong" opens a dialog and sends the reason', fakeAsync(() => {
    let h!: RouterTestingHarness;
    open('/ask?c=3').then((x) => (h = x));
    flush();
    http.expectOne('/api/ask/conversations/3').flush({ id: 3, title: 'x', turns: [askFixture()] });
    h.detectChanges();
    Array.from(el(h).querySelectorAll<HTMLButtonElement>('.actions button')).find((b) => b.textContent?.includes('wrong'))!.click();
    flush();
    TestBed.inject(ApplicationRef).tick();
    const dialog = document.querySelector('mat-dialog-container')!;
    expect(dialog.textContent).toContain("What's wrong with this answer?");
    dialog.querySelector<HTMLInputElement>('input[value=missing_information]')!.click();
    dialog.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    flush();
    TestBed.inject(ApplicationRef).tick();
    const req = http.expectOne('/api/ask/turns/21/feedback');
    expect(req.request.body).toEqual({ helpful: false, reason: 'missing_information', comment: null });
    req.flush({ turn_id: 21, helpful: false });
    flush();
  }));

  it('demo mode: offers the curated questions and turns a refusal into clickable suggestions', async () => {
    const h = await open();
    TestBed.inject(StatusService).status.set(demoStatusFixture());
    h.detectChanges();
    const starters = Array.from(el(h).querySelectorAll('.intro .chip-btn')).map((b) => b.textContent?.trim());
    expect(starters).toEqual(DEMO_QUESTIONS);
    expect(el(h).querySelector('.cost')?.textContent).toContain('from the snapshot');

    type(h, 'What is the weather?');
    http.expectOne('/api/ask').flush(
      { detail: { demo: true, message: 'Demo mode answers a few curated questions.', suggestions: DEMO_QUESTIONS } },
      { status: 409, statusText: 'Conflict' },
    );
    h.detectChanges();
    expect(el(h).querySelector('#ask-error')?.textContent).toContain('Demo mode answers a few curated questions.');
    const chips = el(h).querySelectorAll<HTMLButtonElement>('[aria-label="Questions the demo answers"] .chip-btn');
    expect(chips.length).toBe(2);
    chips[1].click();
    expect(http.expectOne('/api/ask').request.body.question).toBe(DEMO_QUESTIONS[1]);
  });
});
