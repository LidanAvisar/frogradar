import type { Tone } from '../../core/format';

/** Labels and tones for mission status / origin (text always, color only as a second cue). */
export function missionStatusInfo(status: string): { label: string; tone: Tone; live: boolean } {
  switch (status) {
    case 'queued':
      return { label: 'Queued', tone: 'neutral', live: true };
    case 'planning':
      return { label: 'Planning', tone: 'blue', live: true };
    case 'awaiting_approval':
      return { label: 'Plan awaiting approval', tone: 'yellow', live: false };
    case 'running':
      return { label: 'Running', tone: 'green', live: true };
    case 'completed':
      return { label: 'Completed', tone: 'green', live: false };
    case 'budget_exceeded':
      return { label: 'Budget reached', tone: 'orange', live: false };
    case 'cancelled':
      return { label: 'Stopped', tone: 'neutral', live: false };
    case 'failed':
      return { label: 'Failed', tone: 'red', live: false };
    default:
      return { label: status, tone: 'neutral', live: false };
  }
}

export function missionOriginLabel(kind: string): string {
  return (
    {
      manual: 'Manual',
      signal: 'From a signal',
      radar_candidate: 'From the radar',
      capability: 'From a battlecard cell',
      ask_turn: 'From Ask',
    } as Record<string, string>
  )[kind] ?? kind;
}

export function usd(n: number | null | undefined, digits = 2): string {
  return `$${(n ?? 0).toFixed(digits)}`;
}

/** Can the agent still change this mission (poll it)? */
export function isLive(status: string): boolean {
  return missionStatusInfo(status).live;
}
