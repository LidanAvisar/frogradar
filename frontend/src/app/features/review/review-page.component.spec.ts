import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import type { Scorecard, Signal } from '../../api/models';
import { signalFixture } from '../../testing/fixtures';
import { ReviewPageComponent, isTyping } from './review-page.component';
import { SignalActionsService } from './signal-actions.service';

function scorecardFixture(): Scorecard {
  const stats = {
    reviewed: 4, approved: 1, corrected: 2, rejected: 1, approved_pct: 25, corrected_pct: 50, rejected_pct: 25,
    fields: {
      category: { corrected: 1, rate: 0.25 },
      impact: { corrected: 2, rate: 0.5, mean_delta: -1.5 },
      relevance: { corrected: 1, rate: 0.25 },
      entity: { corrected: 0, rate: 0 },
    },
    reasons: [{ code: 'impact_too_high' as const, count: 2 }],
  };
  return { overall: stats, weeks: [{ ...stats, week_start: '2026-10-19' }] };
}

describe('ReviewPageComponent', () => {
  let fixture: ComponentFixture<ReviewPageComponent>;
  let el: HTMLElement;
  let http: HttpTestingController;
  let actions: jasmine.SpyObj<SignalActionsService>;

  beforeEach(() => {
    actions = jasmine.createSpyObj<SignalActionsService>('SignalActionsService', ['act'], {
      changed: of() as never,
      dialogOpen: (() => false) as never,
    });
    TestBed.configureTestingModule({
      providers: [
        provideNoopAnimations(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: SignalActionsService, useValue: actions },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(ReviewPageComponent);
    fixture.detectChanges();
    el = fixture.nativeElement;
    http.expectOne('/api/review?limit=100').flush({
      items: [signalFixture({ id: 1, title: 'First' }), signalFixture({ id: 2, title: 'Second' }), signalFixture({ id: 3, title: 'Third' })],
      progress: { reviewed: 9, total: 12 },
    });
    http.expectOne('/api/feedback/scorecard').flush(scorecardFixture());
    http.match('/api/self').forEach((r) => r.flush({ slug: 'jfrog', name: 'JFrog', product_terms: [], bare_terms: [] }));
    fixture.detectChanges();
  });

  function key(k: string, target: EventTarget = document.body): void {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
    fixture.detectChanges();
  }

  function focusedTitle(): string {
    return el.querySelector('.focus fr-signal-card h3')?.textContent?.trim() ?? '';
  }

  it('shows the scorecard, progress, legend and the first signal in focus', () => {
    expect(el.textContent).toContain('AI vs Analyst');
    expect(el.textContent).toContain('50%');
    expect(el.textContent).toContain('avg −1.5');
    expect(el.textContent).toContain('Impact too high · 2');
    expect(el.textContent).toContain('9 of 12 reviewed');
    expect(el.querySelectorAll('.legend kbd').length).toBe(5);
    expect(focusedTitle()).toContain('First');
  });

  it('J / K move between signals; A approves the one in focus', () => {
    key('j');
    expect(focusedTitle()).toContain('Second');
    key('k');
    expect(focusedTitle()).toContain('First');
    key('a');
    expect(actions.act).toHaveBeenCalledWith('approve', jasmine.objectContaining({ id: 1 }), jasmine.any(Function));
  });

  it('moves on after an action and counts it; a rollback brings the signal back', () => {
    key('c');
    const [, s, apply] = actions.act.calls.mostRecent().args as [string, Signal, (n: Signal | null) => void];
    apply({ ...s, review: { status: 'approved', by: 'dana', at: null, edited_fields: ['impact'] } });
    fixture.detectChanges();
    expect(el.textContent).toContain('10 of 12 reviewed');
    expect(focusedTitle()).toContain('Second');
    apply(s); // server error: original state
    fixture.detectChanges();
    expect(el.textContent).toContain('9 of 12 reviewed');
    expect(focusedTitle()).toContain('First');
  });

  it('never hijacks keys while typing or with modifiers', () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    key('a', input);
    key('j', input);
    input.remove();
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', metaKey: true, bubbles: true }));
    expect(actions.act).not.toHaveBeenCalled();
    expect(focusedTitle()).toContain('First');
    expect(isTyping(document.createElement('textarea'))).toBeTrue();
    expect(isTyping(document.body)).toBeFalse();
  });
});
