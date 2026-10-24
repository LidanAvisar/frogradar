import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import type { MissionDetail, MissionFinding } from '../../api/models';
import { StatusService } from '../../core/status.service';
import { statusFixture } from '../../testing/status-fixture';
import { MissionDetailPageComponent, POLL_MS } from './mission-detail-page.component';
import { MissionsPageComponent } from './missions-page.component';

function finding(over: Partial<MissionFinding> = {}): MissionFinding {
  return {
    id: 7, sub_question_no: 1, statement: 'Sonatype Lifecycle offers reachability analysis for Java.',
    evidence_quote: 'Sonatype Lifecycle adds reachability analysis for Java.',
    source: { id: 3, kind: 'web', url: 'https://www.sonatype.com/blog/r', title: 'Reachability', signal_id: null },
    confidence: 'medium', verification: 'partial', verification_label: 'Partly verified',
    verification_notes: 'Date not stated.', is_claim: false, impact: 3, status: 'new', signal_id: null, reviewed_by: null,
    ...over,
  };
}

function mission(over: Partial<MissionDetail> = {}): MissionDetail {
  return {
    id: 5, title: 'Sonatype reachability', brief: 'Does Sonatype offer reachability analysis?', status: 'running',
    origin_kind: 'capability', origin_ref: { entity_id: 2, capability_id: 9 }, origin_label: 'Battlecard: Sonatype · Reachability',
    origin_link: '/battlecards/sonatype', scope_label: 'Sonatype', company_ids: [2], days: null, budget_usd: 0.3,
    cost_usd: 0.06, max_steps: 8, steps_used: 4, findings_new: 1, findings_approved: 0, created_by: 'Dana',
    sub_questions_total: 2, sub_questions_researched: 1, partial: false, skipped: [],
    created_at: '2026-10-21T08:00:00Z', started_at: '2026-10-21T08:01:00Z', finished_at: null, error: null,
    plan: { sub_questions: [{ question: 'Is reachability in Lifecycle?', search_query: 'q', why: '' }, { question: 'Which languages?', search_query: 'q2', why: '' }], approved: true, approved_by: 'Dana', edited_by_analyst: false },
    steps: [
      { step_no: 1, kind: 'plan', summary: 'Planned 2 sub-questions', cost_usd: 0.01, created_at: '2026-10-21T08:00:10Z', counted: false },
      { step_no: 2, kind: 'search', summary: 'Q1: FrogRadar 0 signals · web 2 results', cost_usd: 0, created_at: '2026-10-21T08:01:00Z', counted: true },
      { step_no: 3, kind: 'verify', summary: 'Q1: verified 1 · 0 supported · 1 partly verified · 0 dropped', cost_usd: 0.002, created_at: '2026-10-21T08:02:00Z', counted: true },
    ],
    findings: [finding()], dropped_count: 1, dropped: ['evidence quote not found in the cited source'], report: null,
    radar_candidate_id: null, stop_requested: false,
    ...over,
  };
}

describe('MissionsPageComponent', () => {
  let http: HttpTestingController;
  let router: Router;

  async function render(url: string) {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: 'missions', component: MissionsPageComponent }, { path: 'missions/:id', component: MissionDetailPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    const fixture = TestBed.createComponent(MissionsPageComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('opens the form pre-filled from a radar candidate and creates the mission with its origin', async () => {
    const fixture = await render('/missions?candidate=11');
    http.expectOne('/api/missions?limit=50').flush({ items: [] });
    http.expectOne('/api/entities').flush([{ id: 2, slug: 'sonatype', name: 'Sonatype', type: 'company', relationship: 'competitor', is_self: false, signal_count: 3, last_signal_at: null }]);
    const pre = http.expectOne((r) => r.url === '/api/missions/prefill');
    expect(pre.request.params.get('candidate')).toBe('11');
    pre.flush({ brief: "What does GitGuardian sell, and how does it overlap with JFrog's products?", origin_kind: 'radar_candidate',
                origin_ref: { candidate_id: 11 }, origin_label: 'Radar candidate: GitGuardian', company_ids: [], days: null,
                budget_usd: 0.3, max_budget_usd: 2 });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Started from: Radar candidate: GitGuardian');
    expect(el.querySelector<HTMLTextAreaElement>('#mission-brief')!.value).toContain('What does GitGuardian sell');
    expect(el.querySelector<HTMLInputElement>('#mission-budget')!.value).toBe('0.3');
    el.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    const post = http.expectOne('/api/missions');
    expect(post.request.body.origin_kind).toBe('radar_candidate');
    expect(post.request.body.origin_ref).toEqual({ candidate_id: 11 });
    expect(post.request.body.budget_usd).toBe(0.3);
    expect(post.request.body.max_steps).toBeUndefined(); // the approved plan sets the step limit
    expect(el.querySelector('#mission-steps')).toBeNull();
    post.flush(mission({ id: 8, status: 'queued' }));
    await fixture.whenStable();
    expect(router.url).toBe('/missions/8');
  });

  it('lists missions with status, origin and cost', async () => {
    const fixture = await render('/missions');
    http.expectOne('/api/missions?limit=50').flush({ items: [mission()] });
    http.expectOne('/api/entities').flush([]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const row = el.querySelector('.mission')!;
    expect(row.textContent).toContain('Running');
    expect(row.textContent).toContain('From a battlecard cell');
    expect(row.textContent).toContain('$0.060 of $0.30');
    expect(row.textContent).toContain('1 findings to review');
    expect(row.querySelector('a')?.getAttribute('href')).toBe('/missions/5');
  });
});

describe('MissionDetailPageComponent', () => {
  let http: HttpTestingController;

  type View = { detectChanges: () => void; nativeElement: HTMLElement };

  async function render(): Promise<View> {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: 'missions/:id', component: MissionDetailPageComponent }]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/missions/5', MissionDetailPageComponent);
    return { detectChanges: () => harness.detectChanges(), nativeElement: harness.routeNativeElement as HTMLElement };
  }

  it('polls every 2 s while running, announces status changes and stops polling when finished', fakeAsync(() => {
    let fixture!: View;
    render().then((f) => (fixture = f));
    tick();
    http.expectOne('/api/missions/5').flush(mission());
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('$0.060');
    expect(el.querySelector('.meter')?.getAttribute('aria-label')).toBe('20% of the budget used');
    expect(el.querySelector('[role=log]')?.textContent).toContain('Q1: verified 1');
    expect(el.querySelector('.finding')?.textContent).toContain('Partly verified');
    expect(el.querySelector('.finding')?.textContent).toContain('Medium confidence');
    tick(POLL_MS);
    http.expectOne('/api/missions/5').flush(mission({ status: 'completed', report: { summary: [{ text: 'Yes, for Java.', finding_ids: [7] }], open_questions: ['npm support?'], stopped_reason: null, generated: 'llm' } }));
    fixture.detectChanges();
    expect(el.querySelector('[aria-live=polite]')?.textContent).toContain('Mission status: Completed.');
    expect(el.querySelector('.report')?.textContent).toContain('Yes, for Java.');
    expect(el.querySelector('.open-question')?.textContent).toContain('npm support?');
    tick(POLL_MS * 3);
    http.expectNone('/api/missions/5');
  }));

  it('lets the analyst edit and approve the plan while awaiting approval', fakeAsync(() => {
    let fixture!: View;
    render().then((f) => (fixture = f));
    tick();
    http.expectOne('/api/missions/5').flush(mission({ status: 'awaiting_approval', steps: [], findings: [], plan: { sub_questions: [{ question: 'Is reachability in Lifecycle?', search_query: 'q', why: '' }, { question: 'Which languages?', search_query: 'q2', why: '' }], approved: false, approved_by: null, edited_by_analyst: false } }));
    fixture.detectChanges();
    tick();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelectorAll('.draft textarea').length).toBe(2);
    TestBed.inject(StatusService).status.set(statusFixture());
    fixture.detectChanges();
    expect(el.querySelector('.plan-estimate')?.textContent).toContain('2 sub-questions · 8 steps');
    expect(el.querySelector('.plan-estimate')?.textContent).toContain('about $0.02 to finish (report + 2 × $0.004, measured)');
    el.querySelector<HTMLButtonElement>('[aria-label="Remove sub-question 2"]')!.click();
    fixture.detectChanges();
    expect(el.querySelector('.plan-estimate')?.textContent).toContain('1 sub-questions · 4 steps');
    Array.from(el.querySelectorAll<HTMLButtonElement>('.plan-actions button')).find((b) => b.textContent?.includes('Approve plan'))!.click();
    const req = http.expectOne('/api/missions/5/plan');
    expect(req.request.body.sub_questions).toEqual([{ question: 'Is reachability in Lifecycle?', search_query: 'q', why: '' }]);
    req.flush(mission({ status: 'queued' }));
    fixture.detectChanges();
    expect(el.querySelector('[aria-live=polite]')?.textContent).toContain('Plan approved');
    tick(POLL_MS);
    http.expectOne('/api/missions/5').flush(mission({ status: 'completed' }));
    tick(POLL_MS);
  }));

  it('a mission cut short says partial and lists the sub-questions that never ran', fakeAsync(() => {
    let fixture!: View;
    render().then((f) => (fixture = f));
    tick();
    const skipped = [{ n: 2, question: 'Which languages?' }];
    http.expectOne('/api/missions/5').flush(mission({
      status: 'completed', partial: true, sub_questions_researched: 1, skipped,
      report: { summary: [], open_questions: [], stopped_reason: 'Step limit reached (4 steps).', generated: 'fallback', skipped },
    }));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const pill = el.querySelector('.chips .fr-pill')!;
    expect(pill.textContent).toContain('Completed – partial');
    expect(pill.className).toContain('tone-orange');
    expect(el.querySelector('.notice--partial')?.textContent).toContain('1 of 2 sub-questions researched');
    expect(el.querySelector('.notice--partial')?.textContent).toContain('Q2 Which languages?');
    expect(el.querySelector('.not-researched')?.textContent).toContain('Q2: Which languages?');
    expect(el.querySelector('.not-researched')?.textContent).toContain('Step limit reached');
  }));

  it('approves a finding optimistically and rolls back on error', fakeAsync(() => {
    let fixture!: View;
    render().then((f) => (fixture = f));
    tick();
    http.expectOne('/api/missions/5').flush(mission({ status: 'completed' }));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[aria-label="Approve finding 1"]')!.click();
    fixture.detectChanges();
    expect(el.querySelector('.finding')?.textContent).toContain('Approved');
    http.expectOne('/api/missions/findings/7/approve').flush('boom', { status: 500, statusText: 'Error' });
    fixture.detectChanges();
    expect(el.querySelector('.finding')?.textContent).not.toContain('Approved');
    expect(el.querySelector('[role=alert]')?.textContent).toContain('could not be approved');
    el.querySelector<HTMLButtonElement>('[aria-label="Approve finding 1"]')!.click();
    http.expectOne('/api/missions/findings/7/approve').flush({ finding: finding({ status: 'approved', signal_id: 99 }), signal_id: 99 });
    fixture.detectChanges();
    expect(el.querySelector('.finding')?.textContent).toContain('signal #99');
  }));
});
