import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { signalFixture } from '../testing/fixtures';
import { SignalCardComponent } from './signal-card.component';

describe('SignalCardComponent', () => {
  let fixture: ComponentFixture<SignalCardComponent>;
  let el: HTMLElement;

  function render(overrides = {}, variant: 'analyst' | 'executive' = 'analyst'): void {
    fixture = TestBed.createComponent(SignalCardComponent);
    fixture.componentRef.setInput('signal', signalFixture(overrides));
    fixture.componentRef.setInput('rank', 1);
    fixture.componentRef.setInput('variant', variant);
    fixture.detectChanges();
    TestBed.inject(HttpTestingController)
      .match('/api/self')
      .forEach((r) => r.flush({ slug: 'jfrog', name: 'JFrog', product_terms: ['JFrog Curation'], bare_terms: ['Curation'] }));
    fixture.detectChanges();
    el = fixture.nativeElement;
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  it('starts collapsed with an accessible chevron button', () => {
    render();
    const button = el.querySelector<HTMLButtonElement>('button.chevron')!;
    const details = el.querySelector<HTMLElement>('.details')!;
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.getAttribute('aria-controls')).toBe(details.id);
    expect(button.getAttribute('aria-label')).toBe('Show details for GitHub package firewall in public preview');
    expect(details.hidden).toBeTrue();
  });

  it('shows the first sentence of "why" as the takeaway', () => {
    render();
    expect(el.querySelector('.takeaway')?.textContent?.trim()).toBe('It overlaps with JFrog Curation.');
  });

  it('expands from the chevron and from the header row', () => {
    render();
    const button = el.querySelector<HTMLButtonElement>('button.chevron')!;
    button.click();
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(button.getAttribute('aria-label')).toContain('Hide details');
    expect(el.querySelector<HTMLElement>('.details')!.hidden).toBeFalse();

    el.querySelector<HTMLElement>('.title')!.click();
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('highlights JFrog products as text, never as markup', () => {
    render({ why_jfrog_cares: 'Hits JFrog Curation <img src=x onerror=alert(1)>. More.' });
    const strong = el.querySelectorAll('.takeaway strong.jf');
    expect(Array.from(strong).map((s) => s.textContent)).toEqual(['JFrog Curation']);
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('.takeaway')?.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('executive variant shows rank, title and takeaway only', () => {
    render({}, 'executive');
    expect(el.querySelector('.rank')?.textContent).toBe('01');
    expect(el.querySelector('.exec-title a')?.getAttribute('target')).toBe('_blank');
    expect(el.querySelector('.exec-takeaway')?.textContent).toContain('It overlaps with JFrog Curation.');
    expect(el.querySelector('fr-impact-badge, fr-confidence-badge, fr-company-chip, button, blockquote')).toBeNull();
  });
});
