import { ApplicationRef } from '@angular/core';
import { TestBed, fakeAsync, flush } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';

import type { RadarCandidateDetail } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { candidateFixture } from '../../testing/fixtures';
import { type CandidatePatch, RadarActionsService } from './radar-actions.service';

describe('RadarActionsService (dialogs with a mocked API)', () => {
  let api: jasmine.SpyObj<ApiService>;
  let service: RadarActionsService;
  let state: RadarCandidateDetail;
  let snackOpen: jasmine.Spy;
  const apply = (p: CandidatePatch) => (state = { ...state, ...p });

  beforeEach(() => {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['watchCandidate', 'approveCandidate', 'dismissCandidate', 'nav']);
    api.nav.and.returnValue(of({ signals: 1, review_queue: 1, radar_new: 0 }));
    TestBed.configureTestingModule({
      providers: [provideNoopAnimations(), provideRouter([]), { provide: ApiService, useValue: api }],
    });
    service = TestBed.inject(RadarActionsService);
    snackOpen = spyOn(TestBed.inject(MatSnackBar), 'open').and.callThrough();
    state = candidateFixture();
  });

  /** Let dialogs / snackbars open, close and re-render (they live outside any fixture). */
  function settle(): void {
    flush();
    TestBed.inject(ApplicationRef).tick();
    flush();
  }

  function dialog(): HTMLElement {
    const all = document.querySelectorAll('mat-dialog-container');
    return all[all.length - 1] as HTMLElement;
  }

  /** Last snackbar message (flush() also runs out its timer, so the element is gone by then). */
  function snack(): string {
    return snackOpen.calls.mostRecent()?.args[0] ?? '';
  }

  it('watch is optimistic and keeps the server result', fakeAsync(() => {
    const reply = new Subject<RadarCandidateDetail>();
    api.watchCandidate.and.returnValue(reply);
    service.watch(state, apply);
    expect(state.status).toBe('watching'); // before the server answers
    reply.next(candidateFixture({ status: 'watching', mentions_count: 4 }));
    reply.complete();
    settle();
    expect(state.mentions_count).toBe(4);
    expect(snack()).toContain('Watching Regshield');
  }));

  it('dismiss: requires a reason, sends reason + comment, then shows dismissed', fakeAsync(() => {
    api.dismissCandidate.and.returnValue(of(candidateFixture({ status: 'dismissed', dismiss_reason: 'too_small' })));
    service.dismiss(state, apply);
    settle();
    const d = dialog();
    expect(d.querySelector('h2')?.textContent).toContain('Dismiss Regshield');
    expect(d.querySelectorAll('input[type=radio]').length).toBe(6);

    d.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(d.textContent).toContain('Choose a reason.');
    expect(api.dismissCandidate).not.toHaveBeenCalled();

    d.querySelector<HTMLInputElement>('input[value=too_small]')!.click();
    const comment = d.querySelector<HTMLTextAreaElement>('textarea')!;
    comment.value = 'tiny';
    comment.dispatchEvent(new Event('input'));
    d.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(api.dismissCandidate).toHaveBeenCalledWith(11, { reason: 'too_small', comment: 'tiny' });
    expect(state.status).toBe('dismissed');
    expect(snack()).toContain('Dismissed Regshield');
  }));

  it('dismiss rolls back when the API fails', fakeAsync(() => {
    api.dismissCandidate.and.returnValue(throwError(() => new Error('down')));
    service.dismiss(state, apply);
    settle();
    dialog().querySelector<HTMLInputElement>('input[value=duplicate]')!.click();
    dialog().querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(state.status).toBe('new');
    expect(state.dismiss_reason).toBeNull();
    expect(snack()).toContain('Could not dismiss Regshield. Nothing was changed.');
  }));

  it('cancel sends nothing', fakeAsync(() => {
    service.dismiss(state, apply);
    settle();
    Array.from(dialog().querySelectorAll('button')).find((b) => b.textContent?.includes('Cancel'))!.click();
    settle();
    expect(api.dismissCandidate).not.toHaveBeenCalled();
    expect(state.status).toBe('new');
  }));

  it('track: competitor by default, edited name, repo source checkbox', fakeAsync(() => {
    api.approveCandidate.and.returnValue(
      of({
        candidate: candidateFixture({ status: 'approved', approved_entity: 'regshield-io' }),
        entity: { slug: 'regshield-io', name: 'Regshield IO', type: 'product', relationship: 'competitor' },
        sources: [],
      }),
    );
    service.track(state, apply);
    settle();
    const d = dialog();
    expect(d.querySelector<HTMLSelectElement>('select')!.value).toBe('competitor');
    expect(d.textContent).toContain('regshield/regshield');
    const name = d.querySelector<HTMLInputElement>('#track-name')!;
    expect(name.value).toBe('Regshield');
    name.value = 'Regshield IO';
    name.dispatchEvent(new Event('input'));
    d.querySelector<HTMLInputElement>('input[type=checkbox]')!.click(); // untick
    d.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(api.approveCandidate).toHaveBeenCalledWith(11, { relationship: 'competitor', name: 'Regshield IO', add_sources: false });
    expect(state.status).toBe('approved');
    expect(state.approved_entity).toBe('regshield-io');
    expect(snack()).toContain('Now tracking Regshield IO as competitor');
  }));

  it('track validates the name and rolls back on error', fakeAsync(() => {
    api.approveCandidate.and.returnValue(throwError(() => new Error('409')));
    service.track(state, apply);
    settle();
    const d = dialog();
    const name = d.querySelector<HTMLInputElement>('#track-name')!;
    name.value = 'R';
    name.dispatchEvent(new Event('input'));
    d.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(d.textContent).toContain('at least 2 characters');
    expect(api.approveCandidate).not.toHaveBeenCalled();
    name.value = 'Regshield';
    name.dispatchEvent(new Event('input'));
    d.querySelector<HTMLButtonElement>('button[type=submit]')!.click();
    settle();
    expect(state.status).toBe('new');
    expect(snack()).toContain('Could not track Regshield');
  }));
});
