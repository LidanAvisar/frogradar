import { TestBed } from '@angular/core/testing';

import { PreferencesService } from './preferences.service';

describe('PreferencesService', () => {
  afterEach(() => localStorage.removeItem('frogradar.executiveView'));

  it('remembers the executive view per browser', () => {
    TestBed.inject(PreferencesService).setExecutiveView(true);
    expect(localStorage.getItem('frogradar.executiveView')).toBe('true');

    TestBed.resetTestingModule();
    expect(TestBed.inject(PreferencesService).executiveView()).toBeTrue();
  });

  it('keeps working when storage throws', () => {
    spyOn(Storage.prototype, 'getItem').and.throwError('blocked');
    spyOn(Storage.prototype, 'setItem').and.throwError('blocked');
    const prefs = TestBed.inject(PreferencesService);
    expect(prefs.executiveView()).toBeFalse();
    prefs.setExecutiveView(true);
    expect(prefs.executiveView()).toBeTrue();
  });
});
