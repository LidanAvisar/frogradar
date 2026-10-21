import type { AskOut, Brief, RadarCandidateDetail, RankedSignal, TopStory } from '../api/models';

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

export function candidateFixture(overrides: Partial<RadarCandidateDetail> = {}): RadarCandidateDetail {
  return {
    id: 11,
    name: 'Regshield',
    kind: 'open_source_project',
    status: 'new',
    homepage: 'https://regshield.io',
    repo_url: 'https://github.com/regshield/regshield',
    score: 61.5,
    breakdown: {
      formula: 'radar_score.v2',
      score: 61.5,
      components: {
        relevance: { value: { max_impact: 4, relation: 'threatens', basis: 'profile_relation' }, factor: 0.92, weight: 0.35, points: 32.2 },
        source_diversity: { value: { sources: 2, weighted: 1.35 }, factor: 0.68, weight: 0.2, points: 13.5 },
        mentions: { value: { mentions: 3, weighted: 1.2 }, factor: 0.4, weight: 0.1, points: 4 },
        mention_velocity: { value: { recent: 1, prior: 0, recent_days: 14 }, factor: 0.33, weight: 0.1, points: 3.3 },
        traction: { value: { stars: 800, stars_per_day: 12.5, stars_basis: 'age', hn_points_max: null, from: 'stars' }, factor: 0.2, weight: 0.1, points: 2 },
        recency: { value: { days_since_last: 1 }, factor: 0.95, weight: 0.15, points: 6.5 },
      },
    },
    sources_count: 2,
    mentions_count: 3,
    first_seen: '2026-10-16T08:00:00Z',
    last_seen: '2026-10-20T08:00:00Z',
    segments: ['supply_chain_security'],
    line: 'Launched on HN this week.',
    line_source: 'why_notable_now',
    what_it_is: 'An open-source registry firewall.',
    why_notable_now: 'Launched on HN this week.',
    relation: 'threatens',
    relation_products: ['JFrog Curation'],
    relation_check: 'supported',
    profile_status: 'ok',
    origins: ['hn_new_entity'],
    metrics: { mentions: 3, sources: 2, mentions_recent: 1, stars: 800, star_velocity: 12.5, star_velocity_basis: 'age', hn_points_max: null, max_impact: 4, relation: 'threatens' },
    statements: [
      { key: 'what_it_is', text: 'An open-source registry firewall.', citations: [{ id: 5, title: 'Show HN: Regshield', url: 'https://news.ycombinator.com/item?id=1', published_at: '2026-10-19T08:00:00Z' }], verification: 'supported', verification_label: 'Verified', confidence: 'high' },
      { key: 'why_notable_now', text: 'Launched on HN this week.', verification: 'partial', verification_label: 'Partly verified', confidence: 'medium', verifier_note: 'The week is not stated.', citations: [{ id: 6, title: 'Regshield 1.0', url: 'https://x.test/6', published_at: null }, { id: 5, title: 'Show HN: Regshield', url: 'https://news.ycombinator.com/item?id=1', published_at: null }] },
    ],
    dropped_statements: 1,
    profile_generated_at: '2026-10-21T08:00:00Z',
    mentions: [{ id: 5, title: 'Show HN: Regshield', url: 'https://news.ycombinator.com/item?id=1', published_at: '2026-10-19T08:00:00Z', source: 'Hacker News (discovery)', origin: 'hn_new_entity' }],
    merges: [],
    other_names: [],
    dismiss_reason: null,
    dismiss_comment: null,
    approved_entity: null,
    ...overrides,
  };
}

export function askFixture(overrides: Partial<AskOut> = {}): AskOut {
  return {
    conversation_id: 3,
    turn_id: 21,
    position: 1,
    question: 'Who launched a package firewall?',
    standalone_question: 'Who launched a package firewall?',
    filters: { company: null, company_slug: null, window: null },
    not_enough_evidence: false,
    intro: 'Two vendors moved security into the registry.',
    points: [
      { lead: 'GitHub firewall preview', text: 'GitHub launched a package firewall in public preview.', citations: [1] },
      { lead: 'Sonatype blocks at entry', text: 'Nexus Pro+ blocks malicious packages.', citations: [2, 1] },
    ],
    meaning_for_jfrog: { text: 'Lead with Curation’s multi-format coverage.', citations: [3] },
    gap: null,
    suggestions: [],
    suggested_followups: ['Compare the two firewalls', 'What does Curation cover?', 'Pricing?'],
    sources: [
      { n: 1, id: 'sig:1046', type: 'article', title: 'GitHub package firewall in public preview', source: 'GitHub Changelog', source_kind: 'official', date: '2026-10-20T10:00:00Z', url: 'https://github.blog/changelog/1', confidence: 0.825, confidence_band: 'high' },
      { n: 2, id: 'sig:52', type: 'article', title: 'Trust What Ships', source: 'Sonatype Blog', source_kind: 'official', date: '2026-10-14T10:00:00Z', url: 'https://x.test/52', confidence: 0.825, confidence_band: 'high' },
      { n: 3, id: 'bc:sonatype:weaknesses:3', type: 'battlecard', title: 'Sonatype battlecard · Weakness', source: 'FrogRadar battlecard', source_kind: null, date: '2026-10-21T08:00:00Z', url: '/battlecards/sonatype', confidence: null, confidence_band: null },
    ],
    retrieved: 8,
    cost_usd: 0.021,
    cached: false,
    created_at: '2026-10-21T08:00:00Z',
    ...overrides,
  };
}
