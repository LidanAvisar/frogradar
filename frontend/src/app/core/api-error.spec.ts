import { demoSuggestions, errorMessage } from './api-error';

describe('api-error helpers', () => {
  it('reads string details, demo objects, and falls back', () => {
    expect(errorMessage({ error: { detail: 'Nope.' } }, 'fallback')).toBe('Nope.');
    expect(errorMessage({ error: { detail: { demo: true, message: 'Demo mode: try these' } } }, 'f')).toBe(
      'Demo mode: try these',
    );
    expect(errorMessage({ error: { detail: [{ loc: ['body'] }] } }, 'fallback')).toBe('fallback');
    expect(errorMessage(null, 'fallback')).toBe('fallback');
  });

  it('returns demo suggestions only for demo refusals', () => {
    expect(demoSuggestions({ error: { detail: { demo: true, suggestions: ['A?'] } } })).toEqual(['A?']);
    expect(demoSuggestions({ error: { detail: { demo: true } } })).toEqual([]);
    expect(demoSuggestions({ error: { detail: 'x' } })).toBeNull();
  });
});
