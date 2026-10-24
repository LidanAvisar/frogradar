import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { askFixture } from '../../testing/fixtures';
import { SourcesRailComponent } from './sources-rail.component';

describe('SourcesRailComponent', () => {
  const item =
    "Counter Nexus Pro+/Enterprise block-at-entry claims by comparing JFrog Curation's policy gate on Artifactory remote repositories in head-to-head deal reviews.";

  async function render() {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(SourcesRailComponent);
    const sources = askFixture().sources.map((s) =>
      s.type === 'battlecard' ? { ...s, title: `Sonatype battlecard · Opportunity for JFrog: ${item}` } : s,
    );
    fixture.componentRef.setInput('sources', sources);
    fixture.componentRef.setInput('turnId', 7);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 30)); // ResizeObserver
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows a battlecard source in full, clamped to 2 lines with "more", linking inside the app', async () => {
    const el = await render();
    const bc = el.querySelector('#src-7-3 fr-clamp-text')!;
    const link = bc.querySelector<HTMLAnchorElement>('a.text')!;
    expect(link.textContent).toBe(`Sonatype battlecard · Opportunity for JFrog: ${item}`); // never sliced
    expect(link.getAttribute('href')).toBe('/battlecards/sonatype');
    expect(link.getAttribute('target')).toBeNull();
    expect(link.style.getPropertyValue('--fr-clamp-lines')).toBe('2');
    expect(bc.querySelector('button.toggle')?.textContent).toContain('more');
  });

  it('keeps article sources as plain external links', async () => {
    const el = await render();
    const a = el.querySelector<HTMLAnchorElement>('#src-7-1 a.name')!;
    expect(a.getAttribute('href')).toBe('https://github.blog/changelog/1');
    expect(a.getAttribute('target')).toBe('_blank');
    expect(el.querySelector('#src-7-1 fr-clamp-text')).toBeNull();
  });
});
