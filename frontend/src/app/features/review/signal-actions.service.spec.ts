import { ApplicationRef } from '@angular/core';
import { TestBed, fakeAsync, flush } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';

import type { Signal } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { PreferencesService } from '../../core/preferences.service';
import { signalFixture } from '../../testing/fixtures';
import { SignalActionsService, SignalOverlay } from './signal-actions.service';

describe('SignalActionsService (mocked API)', () => {
  let api: jasmine.SpyObj<ApiService>;
  let service: SignalActionsService;
  let state: Signal | null;
  let snackOpen: jasmine.Spy;
  const apply = (next: Signal | null) => (state = next);

  beforeEach(() => {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['approveSignal', 'correctSignal', 'rejectSignal', 'nav', 'entities']);
    api.nav.and.returnValue(of({ signals: 1, review_queue: 1, radar_new: 0 }));
    api.entities.and.returnValue(
      of([{ slug: 'sonatype', name: 'Sonatype', type: 'company', relationship: 'competitor', is_self: false, signal_count: 3, last_signal_at: null }]),
    );
    TestBed.configureTestingModule({
      providers: [provideNoopAnimations(), provideRouter([]), { provide: ApiService, useValue: api }],
    });
    try {
      localStorage.removeItem('frogradar.analystName');
    } catch {
      /* storage blocked */
    }
    TestBed.inject(PreferencesService).setAnalystName('dana');
    service = TestBed.inject(SignalActionsService);
    snackOpen = spyOn(TestBed.inject(MatSnackBar), 'open').and.callThrough();
    state = signalFixture();
  });

  function settle(): void {
    flush();
    TestBed.inject(ApplicationRef).tick();
    flush();
  }

  function dialog(): HTMLElement {
    const all = document.querySelectorAll('mat-dialog-container');
    return all[all.length - 1] as HTMLElement;
  }

  function button(root: ParentNode, label: string): HTMLButtonElement {
    return Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === label)!;
  }

  function change(el: HTMLSelectElement | HTMLInputElement, value?: string): void {
    if (value !== undefined) el.value = value;
    el.dispatchEvent(new Event('change'));
    TestBed.inject(ApplicationRef).tick();
  }

  it('approve is optimistic, sends the analyst name and keeps the server result', fakeAsync(() => {
    const reply = new Subject<Signal>();
    api.approveSignal.and.returnValue(reply);
    service.approve(state!, apply);
    expect(state?.review?.status).toBe('approved'); // before the server answers
    expect(api.approveSignal).toHaveBeenCalledWith(1, { analyst: 'dana' });
    reply.next(signalFixture({ review: { status: 'approved', by: 'dana', at: '2026-10-21T10:00:00Z', edited_fields: [] } }));
    reply.complete();
    settle();
    expect(state?.review?.at).toBe('2026-10-21T10:00:00Z');
    expect(snackOpen.calls.mostRecent().args[0]).toContain('Approved');
  }));

  it('rolls back when the server fails', fakeAsync(() => {
    api.approveSignal.and.returnValue(throwError(() => new Error('down')));
    const before = state!;
    service.approve(before, apply);
    settle();
    expect(state).toBe(before);
    expect(snackOpen.calls.mostRecent().args[0]).toContain('Nothing was changed');
    expect(snackOpen.calls.mostRecent().args[2].politeness).toBe('assertive');
  }));

  it('correct: sends only the changed fields with the reason the change implies', fakeAsync(() => {
    api.correctSignal.and.returnValue(of(signalFixture({ impact: 2 })));
    service.correct(state!, apply);
    settle();
    const d = dialog();
    expect(service.dialogOpen()).toBeTrue();
    d.querySelectorAll<HTMLInputElement>('input[name="impact"]')[1].click(); // impact 2 (shown: 4)
    TestBed.inject(ApplicationRef).tick();
    const comment = d.querySelector<HTMLTextAreaElement>('#correct-comment')!;
    comment.value = 'routine update';
    comment.dispatchEvent(new Event('input'));
    button(d, 'Save correction').click();
    settle();
    expect(api.correctSignal).toHaveBeenCalledWith(1, {
      impact: 2,
      reason_code: 'impact_too_high',
      comment: 'routine update',
      analyst: 'dana',
    });
    expect(service.dialogOpen()).toBeFalse();
  }));

  it('correct: no change is an error; the primary company can be changed', fakeAsync(() => {
    const reply = new Subject<Signal>();
    api.correctSignal.and.returnValue(reply);
    service.correct(state!, apply);
    settle();
    const d = dialog();
    button(d, 'Save correction').click();
    TestBed.inject(ApplicationRef).tick();
    expect(d.textContent).toContain('Change at least one field.');
    expect(api.correctSignal).not.toHaveBeenCalled();
    change(d.querySelector<HTMLSelectElement>('#correct-entity')!, 'sonatype');
    button(d, 'Save correction').click();
    settle();
    expect(api.correctSignal.calls.mostRecent().args[1]).toEqual(
      jasmine.objectContaining({ primary_entity: 'sonatype', reason_code: 'wrong_entity' }),
    );
    expect(state?.entities.find((e) => e.primary)).toBeUndefined(); // optimistic: github no longer primary
    expect(state?.review?.edited_fields).toEqual(['primary_entity']);
  }));

  it('reject removes the signal at once and restores it on error', fakeAsync(() => {
    const reply = new Subject<Signal>();
    api.rejectSignal.and.returnValue(reply);
    const before = state!;
    service.reject(before, apply);
    settle();
    button(dialog(), 'Reject').click(); // "Not relevant" is preselected
    settle();
    expect(state).toBeNull();
    expect(api.rejectSignal).toHaveBeenCalledWith(1, { reason_code: 'not_relevant', comment: null, analyst: 'dana' });
    reply.error(new Error('down'));
    settle();
    expect(state).toBe(before);
  }));

  it('SignalOverlay replaces and removes signals in a list', () => {
    const overlay = new SignalOverlay();
    const list = [signalFixture(), signalFixture({ id: 2 })];
    overlay.applier(1)(null);
    expect(overlay.view(list).map((s) => s.id)).toEqual([2]);
    overlay.applier(1)(list[0]);
    expect(overlay.view(list).map((s) => s.id)).toEqual([1, 2]);
  });
});
