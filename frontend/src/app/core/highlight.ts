/**
 * Splits text into plain and highlighted segments for JFrog product names. The template renders the
 * segments as text nodes (no innerHTML), so nothing in the text can become markup.
 */

export interface Segment {
  text: string;
  hit: boolean;
}

export interface HighlightTerms {
  /** Full names and aliases ("JFrog Xray", "Artifactory"): always ours. */
  always: readonly string[];
  /** Bare names other vendors also use ("Advanced Security"): see ownedBySomeoneElse. */
  guarded: readonly string[];
}

export const NO_TERMS: HighlightTerms = { always: [], guarded: [] };

function escape(term: string): string {
  return term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const cache = new Map<string, RegExp | null>();

/** One regex per term set, longest terms first so "JFrog Xray" wins over "Xray". */
function patternFor(terms: readonly string[]): RegExp | null {
  const key = terms.join('\u0000');
  if (!cache.has(key)) {
    const sorted = [...terms].filter((t) => t.trim()).sort((a, b) => b.length - a.length);
    // Whole words only: no letter, digit, _ or - directly before or after.
    cache.set(key, sorted.length ? new RegExp(`(?<![\\w-])(?:${sorted.map(escape).join('|')})(?![\\w-])`, 'g') : null);
  }
  return cache.get(key)!;
}

/** "GitHub Advanced Security", "GitLab Pipelines": a capitalized word right before a bare name. */
function ownedBySomeoneElse(before: string): boolean {
  const prev = before.match(/([A-Z][\w.+-]*)\s+$/);
  return prev !== null && prev[1] !== 'JFrog';
}

export function highlightSegments(text: string | null | undefined, terms: HighlightTerms): Segment[] {
  if (!text) return [];
  const pattern = patternFor([...terms.always, ...terms.guarded]);
  if (!pattern) return [{ text, hit: false }];
  const guarded = new Set(terms.guarded);
  const out: Segment[] = [];
  let last = 0;
  pattern.lastIndex = 0;
  for (let m = pattern.exec(text); m !== null; m = pattern.exec(text)) {
    if (guarded.has(m[0]) && ownedBySomeoneElse(text.slice(0, m.index))) continue;
    if (m.index > last) out.push({ text: text.slice(last, m.index), hit: false });
    out.push({ text: m[0], hit: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), hit: false });
  return out;
}
