import type { SystemStatus } from '../api/models';

export function statusFixture(over: Partial<SystemStatus> = {}): SystemStatus {
  return {
    models: [
      { tier: 'bulk', model: 'anthropic/claude-haiku-5-5', provider: 'anthropic', key_present: true, env_vars: ['ANTHROPIC_API_KEY'] },
      { tier: 'embedding', model: 'openai/text-embedding-3-small', provider: 'openai', key_present: false, env_vars: ['OPENAI_API_KEY'] },
    ],
    extra_keys: { TAVILY_API_KEY: false },
    features: [{ name: 'ask', label: 'Free-form Ask', state: 'off', reason: 'needs OPENAI_API_KEY' }],
    same_family_verify: false,
    warnings: [],
    spend_today_usd: 0.03,
    daily_budget_usd: 2,
    cost_estimates_usd: { ask: 0.023, mission: 0.03, daily_run: 0.08 },
    ...over,
  };
}
