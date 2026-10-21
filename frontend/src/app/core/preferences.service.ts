import { Injectable, signal } from '@angular/core';

const EXECUTIVE_KEY = 'frogradar.executiveView';
const ANALYST_KEY = 'frogradar.analystName';
export const DEFAULT_ANALYST = 'analyst';

/** Per-browser view preferences. Storage can be missing or blocked, so every access is guarded. */
@Injectable({ providedIn: 'root' })
export class PreferencesService {
  readonly executiveView = signal<boolean>(read(EXECUTIVE_KEY) === 'true');

  /** Name written on feedback rows (no auth yet): the "Your name" field in the sidebar. */
  readonly analystName = signal<string>(read(ANALYST_KEY)?.trim() || DEFAULT_ANALYST);

  setExecutiveView(on: boolean): void {
    this.executiveView.set(on);
    write(EXECUTIVE_KEY, String(on));
  }

  setAnalystName(name: string): void {
    const clean = name.trim().slice(0, 100) || DEFAULT_ANALYST;
    this.analystName.set(clean);
    write(ANALYST_KEY, clean);
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
