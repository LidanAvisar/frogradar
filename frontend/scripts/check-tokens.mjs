// Fails when a color literal (hex, rgb(), hsl()) appears outside src/styles/tokens.css.
// Components must use the var(--fr-…) tokens. Usage: npm run lint:tokens
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = new URL('../src/', import.meta.url).pathname;
const ALLOWED = new Set(['styles/tokens.css']);
const SKIP_DIRS = new Set(['api']); // generated files
const EXT = /\.(s?css|ts|html)$/;
const COLOR = /(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?)\(/g;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (!SKIP_DIRS.has(name)) yield* files(path);
    } else if (EXT.test(name)) {
      yield path;
    }
  }
}

let errors = 0;
for (const path of files(ROOT)) {
  const rel = relative(ROOT, path);
  if (ALLOWED.has(rel) || rel.endsWith('.spec.ts')) continue;
  readFileSync(path, 'utf8').split('\n').forEach((line, i) => {
    for (const m of line.matchAll(COLOR)) {
      console.error(`${rel}:${i + 1}: color literal "${m[0]}" – use a token from styles/tokens.css`);
      errors++;
    }
  });
}
if (errors) process.exit(1);
console.log('tokens: no stray color literals');
