import { HighlightTerms, highlightSegments } from './highlight';

const TERMS: HighlightTerms = {
  always: ['JFrog Advanced Security', 'JFrog Artifactory', 'Artifactory', 'JFrog Curation', 'JFrog Pipelines', 'JFrog Xray', 'Xray'],
  guarded: ['Advanced Security', 'Curation', 'Pipelines'],
};

function hits(text: string, terms = TERMS): string[] {
  return highlightSegments(text, terms).filter((s) => s.hit).map((s) => s.text);
}

describe('highlightSegments', () => {
  it('marks full names, preferring the longest match', () => {
    expect(hits('It overlaps with JFrog Curation and JFrog Xray policies.')).toEqual(['JFrog Curation', 'JFrog Xray']);
  });

  it('aliases are always ours, even after a capitalized word', () => {
    expect(hits('Compare Artifactory with Nexus. Use Xray.')).toEqual(['Artifactory', 'Xray']);
  });

  it('keeps all text, in order', () => {
    const text = 'Compare Artifactory’s remote repositories with Nexus.';
    expect(highlightSegments(text, TERMS).map((s) => s.text).join('')).toBe(text);
    expect(hits(text)).toEqual(['Artifactory']);
  });

  it("does not mark another vendor's product with the same bare name", () => {
    expect(hits('GitHub Advanced Security adds AI secret detection; GitLab Pipelines too.')).toEqual([]);
    expect(hits('It competes with JFrog Advanced Security and Advanced Security add-ons.')).toEqual([
      'JFrog Advanced Security',
      'Advanced Security',
    ]);
  });

  it('matches whole words only', () => {
    expect(hits('Xrays and Curations are not product names; pre-Curation neither.')).toEqual([]);
  });

  it('treats terms as text, not regex', () => {
    expect(hits('Use C++ (beta) here', { always: ['C++ (beta)'], guarded: [] })).toEqual(['C++ (beta)']);
    expect(hits('anything', { always: ['.*'], guarded: [] })).toEqual([]);
  });

  it('handles empty input and no terms', () => {
    expect(highlightSegments('', TERMS)).toEqual([]);
    expect(highlightSegments(null, TERMS)).toEqual([]);
    expect(highlightSegments('plain', { always: [], guarded: [] })).toEqual([{ text: 'plain', hit: false }]);
  });
});
