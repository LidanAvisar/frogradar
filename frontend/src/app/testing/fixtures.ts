import type { Brief, RankedSignal } from '../api/models';

export function signalFixture(overrides: Partial<RankedSignal> = {}): RankedSignal {
  return {
    id: 1,
    rank: 1,
    title: 'GitHub package firewall in public preview',
    original_title: 'GitHub package firewall in public preview',
    url: 'https://github.blog/changelog/1',
    kind: 'article',
    source: { name: 'GitHub Changelog', kind: 'official', trust: 0.9 },
    published_at: '2026-10-20T10:00:00Z',
    category: 'product_launch',
    impact: 4,
    is_claim: false,
    summary: 'GitHub launched a package firewall.',
    why_jfrog_cares: 'It overlaps with JFrog Curation. Battlecards should cover it.',
    takeaway: null,
    evidence: { quote: 'GitHub is introducing a new package firewall.', verified: true, side: 'after', section: null },
    verification: 'supported',
    confidence: { score: 0.825, band: 'high' },
    entities: [{ slug: 'github', name: 'GitHub', type: 'company', primary: true }],
    sources_count: 1,
    page_diff: null,
    ...overrides,
  };
}

export function briefFixture(overrides: Partial<Brief> = {}): Brief {
  return {
    generated_at: '2026-10-21T08:00:00Z',
    window: { days: 7, since: '2026-10-14T08:00:00Z' },
    headline: null,
    overview: null,
    headline_citations: [],
    overview_sentences: [],
    top: [signalFixture(), signalFixture({ id: 2, rank: 2, title: 'Second', is_claim: true })],
    since_yesterday: [
      { id: 1, title: 'GitHub package firewall', url: 'https://x.test/1', impact: 4, category: 'product_launch', entities: [], published_at: '2026-10-21T01:00:00Z' },
    ],
    since_yesterday_total: 3,
    since_yesterday_high_impact: 2,
    filtered_not_relevant: 30,
    pricing_watch: [],
    pipeline: {
      last_run: { id: 30, started_at: '2026-10-21T09:09:05Z', finished_at: '2026-10-21T09:09:46Z', status: 'succeeded' },
      active_feeds: 3,
      watched_pages: 2,
    },
    ...overrides,
  };
}
