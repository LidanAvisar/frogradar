/** Reading FastAPI error bodies: `detail` is a string, or {message, suggestions, demo} in demo mode. */

interface DemoDetail {
  demo?: boolean;
  message?: string;
  suggestions?: string[];
}

function detailOf(e: unknown): unknown {
  return (e as { error?: { detail?: unknown } } | null)?.error?.detail;
}

/** The server's message, or the fallback (never a raw object). */
export function errorMessage(e: unknown, fallback: string): string {
  const detail = detailOf(e);
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && typeof (detail as DemoDetail).message === 'string') {
    return (detail as DemoDetail).message as string;
  }
  return fallback;
}

/** Demo-mode refusal: its suggestions (possibly empty), or null for any other error. */
export function demoSuggestions(e: unknown): string[] | null {
  const detail = detailOf(e);
  if (detail && typeof detail === 'object' && (detail as DemoDetail).demo) {
    return (detail as DemoDetail).suggestions ?? [];
  }
  return null;
}
