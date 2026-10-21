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

const CATEGORY_LABELS: Record<string, string> = {
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
