import { TestBed } from '@angular/core/testing';

import { PreferencesService } from './preferences.service';

describe('PreferencesService', () => {
  afterEach(() => {
    localStorage.removeItem('frogradar.executiveView');
    localStorage.removeItem('frogradar.analystName');
  });

  it('remembers the analyst name, default "analyst"', () => {
    localStorage.removeItem('frogradar.analystName');
    const prefs = TestBed.inject(PreferencesService);
    expect(prefs.analystName()).toBe('analyst');
    prefs.setAnalystName('  Dana  ');
    expect(localStorage.getItem('frogradar.analystName')).toBe('Dana');
    TestBed.resetTestingModule();
    expect(TestBed.inject(PreferencesService).analystName()).toBe('Dana');
    TestBed.inject(PreferencesService).setAnalystName('   ');
    expect(TestBed.inject(PreferencesService).analystName()).toBe('analyst');
  });

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
    expect(prefs.analystName()).toBe('analyst');
    prefs.setAnalystName('Lee');
    expect(prefs.analystName()).toBe('Lee');
    prefs.setExecutiveView(true);
    expect(prefs.executiveView()).toBeTrue();
  });
});
