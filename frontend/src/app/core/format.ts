/** Display helpers shared by every screen. Dates render in the viewer's time zone. */

/** "WEDNESDAY · 21 OCTOBER 2026" */
export function eyebrowDate(iso: string): string {
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('en-GB', { weekday: 'long' });
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  return `${weekday} · ${date}`.toUpperCase();
}

/** "20 Oct 2026" */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "21 Oct, 09:09" */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  return `${date}, ${time}`;
}

export const CATEGORY_LABELS: Record<string, string> = {
  product_launch: 'Product launch',
  feature_update: 'Feature update',
  pricing_change: 'Pricing change',
  partnership: 'Partnership',
  acquisition: 'Acquisition',
  funding: 'Funding',
  security_incident: 'Security incident',
  leadership_change: 'Leadership change',
  customer_win: 'Customer win',
  open_source: 'Open source',
  event: 'Event',
  analyst_report: 'Analyst report',
  other: 'Other',
};

export function categoryLabel(category: string | null | undefined): string {
  return category ? (CATEGORY_LABELS[category] ?? category.replace(/_/g, ' ')) : '';
}

const VERIFICATION_LABELS: Record<string, string> = {
  supported: 'Verified by a second model',
  partial: 'Partly verified',
  pending: 'Not verified yet',
  unsupported: 'Not supported by the source',
};

export function verificationLabel(v: string): string {
  return VERIFICATION_LABELS[v] ?? v;
}

/** First sentence, capped, for one-line views. */
export function firstSentence(text: string | null | undefined, max = 180): string {
  if (!text) return '';
  const m = text.match(/^.*?[.!?](\s|$)/);
  const s = (m ? m[0] : text).trim();
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

/** "supply_chain_security" → "Supply chain security" */
export function humanize(slug: string | null | undefined): string {
  if (!slug) return '';
  const s = slug.replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const SEGMENT_LABELS: Record<string, string> = {
  artifact_management: 'Artifact management',
  supply_chain_security: 'Supply-chain security',
  mlops: 'MLOps',
  cicd: 'CI/CD',
  distribution: 'Software distribution',
};

export function segmentLabel(segment: string): string {
  return SEGMENT_LABELS[segment] ?? humanize(segment);
}

export type Tone = 'red' | 'orange' | 'blue' | 'green' | 'yellow' | 'neutral';

const RELATIONSHIPS: Record<string, { label: string; tone: Tone }> = {
  competitor: { label: 'Direct competitor', tone: 'red' },
  coopetition: { label: 'Coopetition', tone: 'orange' },
  partner: { label: 'Partner', tone: 'green' },
  self: { label: 'JFrog', tone: 'neutral' },
};

/** Company relationship to JFrog (config / approved entity). */
export function relationshipInfo(rel: string | null | undefined): { label: string; tone: Tone } {
  return RELATIONSHIPS[rel ?? ''] ?? { label: humanize(rel), tone: 'neutral' };
}

const RELATIONS: Record<string, { label: string; tone: Tone }> = {
  threatens: { label: 'Threatens', tone: 'red' },
  both: { label: 'Threatens and complements', tone: 'orange' },
  complements: { label: 'Complements', tone: 'green' },
  none: { label: 'No JFrog overlap', tone: 'neutral' },
};

/** Radar profile relation to JFrog products. */
export function relationInfo(rel: string | null | undefined): { label: string; tone: Tone } {
  return RELATIONS[rel ?? ''] ?? { label: 'Relation not assessed', tone: 'neutral' };
}

const KINDS: Record<string, string> = {
  company: 'Company',
  product: 'Product',
  open_source_project: 'Open-source project',
};

export function kindLabel(kind: string): string {
  return KINDS[kind] ?? humanize(kind);
}

const STATUSES: Record<string, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'blue' },
  watching: { label: 'Watching', tone: 'green' },
  approved: { label: 'Tracked', tone: 'green' },
  dismissed: { label: 'Dismissed', tone: 'neutral' },
};

export function radarStatusInfo(status: string): { label: string; tone: Tone } {
  return STATUSES[status] ?? { label: humanize(status), tone: 'neutral' };
}

export const DISMISS_REASONS: { value: string; label: string; hint: string }[] = [
  { value: 'not_relevant', label: 'Not relevant', hint: 'Nothing to do with JFrog’s market' },
  { value: 'not_a_competitor', label: 'Not a competitor', hint: 'Related, but neither threat nor partner' },
  { value: 'too_small', label: 'Too small', hint: 'Too early or too little traction to track' },
  { value: 'duplicate', label: 'Duplicate', hint: 'Same player as another candidate' },
  { value: 'already_tracked', label: 'Already tracked', hint: 'We track it under another name' },
  { value: 'other', label: 'Other', hint: 'Explain in the comment' },
];

export function dismissReasonLabel(reason: string | null | undefined): string {
  return DISMISS_REASONS.find((r) => r.value === reason)?.label ?? humanize(reason);
}

/** "3 days ago" / "today" for first-seen style dates. */
export function relativeDays(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const days = Math.floor((now.getTime() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 60) return `${days} days ago`;
  return shortDate(iso);
}

/** "OCT 2026" */
export function monthYear(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }).toUpperCase();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const ORIGINS: Record<string, string> = {
  news_new_entity: 'News',
  hn_new_entity: 'Hacker News',
  github_search: 'GitHub search',
  cncf_landscape: 'CNCF landscape',
};

/** How a mention reached the radar. */
export function originLabel(origin: string | null | undefined): string {
  return origin ? (ORIGINS[origin] ?? humanize(origin)) : '';
}
