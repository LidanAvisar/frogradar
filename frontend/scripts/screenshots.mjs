// Screenshots of the main screens at desktop (1440) and phone (390) width, with the local Google
// Chrome. Needs the API on :8000 and `ng serve` (default :4200).
// Usage: node scripts/screenshots.mjs [outDir] [name-filter]
// Env: FRONTEND_URL (default http://localhost:4200), API_URL (default http://localhost:8000),
//      EXEC=1 also captures the executive Brief, ASK_C = the Ask conversation to show.
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const base = process.env.FRONTEND_URL ?? 'http://localhost:4200';
const api = process.env.API_URL ?? 'http://localhost:8000';
const outDir = resolve(process.argv[2] ?? '../docs/screenshots');
const only = process.argv[3] ?? '';
mkdirSync(outDir, { recursive: true });

// /ask shows conversation ASK_C, else the most recent one.
const convs = await fetch(`${api}/api/ask/conversations?limit=1`).then((r) => r.json()).catch(() => []);
const askId = process.env.ASK_C ?? convs?.[0]?.id;

// The Radar detail shows the top-ranked open candidate.
const radar = await fetch(`${api}/api/radar?limit=1`).then((r) => r.json()).catch(() => null);
const topId = radar?.items?.[0]?.id;

const pages = [
  { name: 'brief', path: '/', ready: 'fr-signal-card, fr-empty-state, fr-error-state' },
  { name: 'radar', path: '/radar', ready: 'fr-radar-row, fr-empty-state, fr-error-state' },
  ...(topId ? [{ name: 'radar-detail', path: `/radar/${topId}`, ready: '.statements, fr-empty-state, fr-error-state' }] : []),
  { name: 'battlecard-sonatype', path: '/battlecards/sonatype', ready: 'fr-capability-matrix, fr-empty-state, fr-error-state' },
  { name: 'signals-filtered', path: '/signals?since=7d&min_impact=4', ready: 'fr-signal-card, fr-empty-state, fr-error-state' },
  ...(askId ? [{ name: 'ask', path: `/ask?c=${askId}`, ready: 'fr-answer-card' }] : []),
  { name: 'review', path: '/review', ready: 'fr-signal-card, fr-empty-state, fr-error-state' },
  // A card an analyst corrected ("Edited by analyst"); EDITED_Q = its search text.
  { name: 'signal-edited', path: `/signals?q=${encodeURIComponent(process.env.EDITED_Q ?? 'Xcode runner')}`,
    ready: '.review-badge, fr-empty-state, fr-error-state' },
];
const shots = pages.flatMap((p) => [
  { ...p, file: `${p.name}-1440.png`, width: 1440, height: 900, executive: false },
  { ...p, file: `${p.name}-390.png`, width: 390, height: 844, executive: false },
]);
if (process.env.EXEC === '1') {
  shots.push({ ...pages[0], file: 'brief-1440-executive.png', width: 1440, height: 900, executive: true });
}

const browser = await chromium.launch({ channel: 'chrome' });
try {
  for (const shot of shots.filter((s) => s.file.includes(only))) {
    const context = await browser.newContext({
      viewport: { width: shot.width, height: shot.height },
      deviceScaleFactor: 1,
    });
    await context.addInitScript((on) => {
      try {
        localStorage.setItem('frogradar.executiveView', String(on));
      } catch {
        /* storage blocked: default view */
      }
    }, shot.executive);
    const page = await context.newPage();
    await page.goto(base + shot.path, { waitUntil: 'networkidle' });
    await page.waitForSelector(shot.ready);
    await page.evaluate(() => document.fonts.ready);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    if (overflow > 0) console.warn(`${shot.file}: horizontal overflow of ${overflow}px`);
    // Grow the viewport to the page height instead of fullPage: full-page capture leaves the
    // sticky sidebar's background at the first screen height.
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: shot.width, height });
    await page.waitForTimeout(200);
    await page.screenshot({ path: resolve(outDir, shot.file) });
    console.log(`saved ${shot.file}`);
    await context.close();
  }
} finally {
  await browser.close();
}
