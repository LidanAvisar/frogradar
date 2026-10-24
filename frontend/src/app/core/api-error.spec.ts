import { errorMessage } from './api-error';

describe('errorMessage', () => {
  it('reads string details, message objects, and falls back', () => {
    expect(errorMessage({ error: { detail: 'Not found' } }, 'f')).toBe('Not found');
    expect(errorMessage({ error: { detail: { message: 'Ask is off: needs OPENAI_API_KEY.' } } }, 'f')).toBe(
      'Ask is off: needs OPENAI_API_KEY.',
    );
    expect(errorMessage({ error: { detail: { other: 1 } } }, 'fallback')).toBe('fallback');
    expect(errorMessage(null, 'fallback')).toBe('fallback');
  });
});
