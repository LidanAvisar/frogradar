// Screenshots of the Morning Brief at desktop and phone width, with the local Google Chrome.
// Needs the API on :8000 and `ng serve` on :4200. Usage: node scripts/screenshots.mjs [outDir]
// Env: FRONTEND_URL (default http://localhost:4200), EXEC=1 also captures the executive view.
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const base = process.env.FRONTEND_URL ?? 'http://localhost:4200';
const outDir = resolve(process.argv[2] ?? '../docs/screenshots');
mkdirSync(outDir, { recursive: true });

const shots = [
  { name: 'brief-1440.png', width: 1440, height: 900, executive: false },
  { name: 'brief-390.png', width: 390, height: 844, executive: false },
];
if (process.env.EXEC === '1') {
  shots.push({ name: 'brief-1440-executive.png', width: 1440, height: 900, executive: true });
}

const browser = await chromium.launch({ channel: 'chrome' });
try {
  for (const shot of shots) {
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
    await page.goto(base + '/', { waitUntil: 'networkidle' });
    await page.waitForSelector('fr-signal-card, fr-empty-state, fr-error-state');
    await page.evaluate(() => document.fonts.ready);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    if (overflow > 0) console.warn(`${shot.name}: horizontal overflow of ${overflow}px`);
    // Grow the viewport to the page height instead of fullPage: full-page capture leaves the
    // sticky sidebar's background at the first screen height.
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: shot.width, height });
    await page.waitForTimeout(200);
    await page.screenshot({ path: resolve(outDir, shot.name) });
    console.log(`saved ${shot.name}`);
    await context.close();
  }
} finally {
  await browser.close();
}
