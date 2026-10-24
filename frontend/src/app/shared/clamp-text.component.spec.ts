import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { firstSentence } from '../core/format';
import { ClampTextComponent } from './clamp-text.component';

@Component({
  standalone: true,
  imports: [ClampTextComponent],
  template: `<div [style.width.px]="width"><fr-clamp-text [text]="text" /></div>`,
})
class HostComponent {
  width = 160;
  text = '';
}

@Component({
  standalone: true,
  imports: [ClampTextComponent],
  template: `<div style="width: 140px"><fr-clamp-text [lines]="2" href="https://x.test/1"
    text="Sonatype's official documentation describes a feature called Reachability Analysis, which labels vulnerable components" /></div>`,
})
class LinkHostComponent {}

describe('ClampTextComponent', () => {
  async function render(text: string, width = 160) {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.componentInstance.text = text;
    fixture.componentInstance.width = width;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    await new Promise((r) => setTimeout(r, 30)); // ResizeObserver
    fixture.detectChanges();
    return fixture;
  }

  it('shows the whole text, clamps a long one to 3 lines and toggles it with a keyboard-accessible button', async () => {
    const long = 'Nexus Repository supports Docker, npm, PyPI, Maven, NuGet, Helm, Go, RubyGems, Conda, Cargo and more formats in every edition it sells.';
    const fixture = await render(long);
    const el = fixture.nativeElement as HTMLElement;
    const text = el.querySelector<HTMLElement>('.text')!;
    expect(text.textContent).toBe(long); // never sliced
    expect(text.getAttribute('title')).toBe(long);
    expect(text.classList).toContain('clamped');
    const button = el.querySelector<HTMLButtonElement>('button.toggle')!;
    expect(button.textContent).toContain('more');
    expect(button.getAttribute('aria-expanded')).toBe('false');
    button.click();
    fixture.detectChanges();
    expect(text.classList).not.toContain('clamped');
    expect(button.textContent).toContain('less');
    expect(button.getAttribute('aria-expanded')).toBe('true');
  });

  it('renders the text as an external link with the toggle outside it', async () => {
    const fixture = TestBed.createComponent(LinkHostComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 30));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const a = el.querySelector<HTMLAnchorElement>('a.text')!;
    expect(a.getAttribute('href')).toBe('https://x.test/1');
    expect(a.getAttribute('target')).toBe('_blank');
    expect(a.style.getPropertyValue('--fr-clamp-lines')).toBe('2');
    const button = el.querySelector('button.toggle')!;
    expect(button).not.toBeNull();
    expect(a.contains(button)).toBeFalse();
    // The button's visually-hidden label must stay inside scroll containers (it widened the page at 390 px).
    expect(getComputedStyle(el.querySelector('fr-clamp-text')!).position).toBe('relative');
  });

  it('shows no toggle when the text fits', async () => {
    const fixture = await render('Short note.', 400);
    expect((fixture.nativeElement as HTMLElement).querySelector('button.toggle')).toBeNull();
  });
});

describe('firstSentence', () => {
  it('returns the whole first sentence, never a sliced one', () => {
    const s = 'A'.repeat(300) + ' end of a very long first sentence. Second.';
    expect(firstSentence(s)).toBe('A'.repeat(300) + ' end of a very long first sentence.');
    expect(firstSentence('No full stop')).toBe('No full stop');
  });
});
