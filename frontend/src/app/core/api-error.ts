/** Reading FastAPI error bodies: `detail` is a string, or an object with a `message`. */

interface DetailObject {
  message?: string;
}

function detailOf(e: unknown): unknown {
  return (e as { error?: { detail?: unknown } } | null)?.error?.detail;
}

/** The server's message, or the fallback (never a raw object). */
export function errorMessage(e: unknown, fallback: string): string {
  const detail = detailOf(e);
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && typeof (detail as DetailObject).message === 'string') {
    return (detail as DetailObject).message as string;
  }
  return fallback;
}
