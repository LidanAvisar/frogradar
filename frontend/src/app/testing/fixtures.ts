import type { Brief, RankedSignal, TopStory } from '../api/models';

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
    stats: { new_signals: 3, high_impact: 2, price_changes: 1, price_change_days: 7 },
    top_story: null,
    developments: [signalFixture(), signalFixture({ id: 2, rank: 2, title: 'Second', is_claim: true })],
    since_yesterday: [
      { id: 1, title: 'GitHub package firewall', url: 'https://x.test/1', impact: 4, category: 'product_launch', entities: [], published_at: '2026-10-21T01:00:00Z' },
    ],
    filtered_not_relevant: 30,
    pricing_watch: [],
    radar: [
      { id: 11, name: 'Regshield', status: 'new', score: 61.5, sources_count: 3, line: 'Presented on Hacker News this week as a registry firewall.' },
      { id: 12, name: 'Pkgguard', status: 'watching', score: 40.2, sources_count: 1, line: null },
    ],
    pipeline: {
      last_run: { id: 30, started_at: '2026-10-21T09:09:05Z', finished_at: '2026-10-21T09:09:46Z', status: 'succeeded' },
      active_feeds: 3,
      watched_pages: 2,
    },
    ...overrides,
  };
}

export function storyFixture(overrides: Partial<TopStory> = {}): TopStory {
  const a = { id: 7, title: 'GitHub package firewall', url: 'https://s.test/7', published_at: null };
  const b = { id: 9, title: 'GitHub license compliance', url: 'https://s.test/9', published_at: null };
  return {
    source: 'daily_brief',
    title: 'GitHub adds a package firewall that overlaps with Curation',
    title_citations: [a],
    what_happened: { text: 'GitHub previewed an npm-only package firewall.', citations: [a, b] },
    why_it_matters: { text: 'It overlaps with JFrog Curation.', citations: [a] },
    suggested_move: { text: 'Sales: add the npm-only limit to the Curation battlecard.', citations: [b] },
    signal: signalFixture({ id: 7 }),
    company: 'GitHub',
    affects: ['JFrog Curation'],
    signal_ids: [7, 9],
    ...overrides,
  };
}
