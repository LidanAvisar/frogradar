// Screenshots of one mission page at 1440 and 390 px (local Chrome). Needs the API and `ng serve`.
// Usage: node scripts/mission-shots.mjs <missionId> <name> [outDir]
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const [id, name] = process.argv.slice(2, 4);
const outDir = resolve(process.argv[4] ?? '../docs/screenshots');
const base = process.env.FRONTEND_URL ?? 'http://localhost:4200';
mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome' });
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 844 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await page.goto(`${base}/missions/${id}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.tiles, fr-error-state, fr-empty-state');
    await page.waitForTimeout(400);
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width, height });
    await page.screenshot({ path: `${outDir}/${name}-${width}.png` });
    await context.close();
  }
} finally {
  await browser.close();
}
