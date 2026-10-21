import { Injectable, signal } from '@angular/core';

const EXECUTIVE_KEY = 'frogradar.executiveView';

/** Per-browser view preferences. Storage can be missing or blocked, so every access is guarded. */
@Injectable({ providedIn: 'root' })
export class PreferencesService {
  readonly executiveView = signal<boolean>(read(EXECUTIVE_KEY) === 'true');

  setExecutiveView(on: boolean): void {
    this.executiveView.set(on);
    write(EXECUTIVE_KEY, String(on));
  }
}

function read(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
}
