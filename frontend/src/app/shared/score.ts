import type { RadarBreakdown, RadarScoreComponent } from '../api/models';
import { relationInfo } from '../core/format';

export interface ScorePart {
  key: string;
  label: string;
  points: number;
  max: number;
  /** One sentence: what the inputs were. */
  explanation: string;
}

/** Display order and labels of the radar_score components (config/discovery.yaml weights). */
const PARTS: { key: string; label: string }[] = [
  { key: 'relevance', label: 'Relevance' },
  { key: 'source_diversity', label: 'Sources' },
  { key: 'mentions', label: 'Mentions' },
  { key: 'mention_velocity', label: 'Velocity' },
  { key: 'traction', label: 'Traction' },
  { key: 'recency', label: 'Recency' },
];

const round1 = (n: number) => Math.round(n * 10) / 10;

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function explain(key: string, c: RadarScoreComponent): string {
  const v = c.value ?? {};
  switch (key) {
    case 'relevance': {
      const impact = num(v['max_impact']);
      const impactText = impact === null ? 'no enriched mention yet' : `highest mention impact ${impact} of 5`;
      if (v['basis'] === 'profile_relation') {
        return `The profile's JFrog relation: ${relationInfo(String(v['relation'])).label.toLowerCase()} JFrog products; ${impactText}.`;
      }
      return `No verified JFrog relation yet, so relevance comes from impact only: ${impactText}.`;
    }
    case 'source_diversity': {
      const n = num(v['sources']) ?? 0;
      return `${n} distinct ${n === 1 ? 'source' : 'sources'} (${num(v['weighted']) ?? 0} after weighting press and official sources above community and discovery sources).`;
    }
    case 'mentions': {
      const n = num(v['mentions']) ?? 0;
      return `${n} ${n === 1 ? 'mention' : 'mentions'} (${num(v['weighted']) ?? 0} weighted by source credibility).`;
    }
    case 'mention_velocity': {
      const recent = num(v['recent']) ?? 0;
      return `${recent} ${recent === 1 ? 'mention' : 'mentions'} in the last ${num(v['recent_days']) ?? 14} days, ${num(v['prior']) ?? 0} in the weeks before.`;
    }
    case 'traction': {
      if (v['from'] === 'stars') {
        return `${num(v['stars'])?.toLocaleString('en-GB') ?? '?'} GitHub stars, about ${round1(num(v['stars_per_day']) ?? 0)} per day.`;
      }
      if (v['from'] === 'hn') return `Best Hacker News post: ${num(v['hn_points_max']) ?? 0} points.`;
      return 'No GitHub stars or Hacker News points found.';
    }
    case 'recency': {
      const d = num(v['days_since_last']);
      if (d === null) return 'Last mention date unknown.';
      const days = Math.round(d);
      return days < 1 ? 'Mentioned in the last day.' : `Last mentioned ${days} ${days === 1 ? 'day' : 'days'} ago.`;
    }
    default:
      return '';
  }
}

/** The score components in display order; unknown components are appended. */
export function scoreParts(b: RadarBreakdown | null | undefined): ScorePart[] {
  if (!b) return [];
  const known = PARTS.filter((p) => b.components[p.key]);
  const extra = Object.keys(b.components)
    .filter((k) => !PARTS.some((p) => p.key === k))
    .map((k) => ({ key: k, label: k.replace(/_/g, ' ') }));
  return [...known, ...extra].map(({ key, label }) => {
    const c = b.components[key];
    return {
      key,
      label,
      points: round1(c.points),
      max: round1(100 * c.weight),
      explanation: explain(key, c),
    };
  });
}

export function scoreText(score: number | null | undefined): string {
  return score == null ? '–' : Math.round(score).toString();
}
