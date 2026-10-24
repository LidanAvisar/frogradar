import { ChangeDetectionStrategy, Component, ElementRef, effect, input, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * Full text, never sliced: clamped to `lines` lines with CSS, plus a small "more" / "less" toggle
 * (a real button, keyboard-accessible) that appears only when the text actually overflows. The full
 * text is also the element's title, as a fallback. With `href` the text is an external link (opens in
 * a new tab), with `route` a link inside the app; the toggle stays outside the link.
 */
@Component({
  selector: 'fr-clamp-text',
  standalone: true,
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (route()) {
      <a #box class="text internal" [class.clamped]="!expanded()" [style.--fr-clamp-lines]="lines()" [attr.title]="text()"
         [routerLink]="route()">{{ text() }}</a>
    } @else if (href()) {
      <a #box class="text" [class.clamped]="!expanded()" [style.--fr-clamp-lines]="lines()" [attr.title]="text()"
         [href]="href()" target="_blank" rel="noopener noreferrer">{{ text() }}</a>
    } @else {
      <span #box class="text" [class.clamped]="!expanded()" [style.--fr-clamp-lines]="lines()" [attr.title]="text()">{{ text() }}</span>
    }
    @if (overflows() || expanded()) {
      <button type="button" class="toggle" [attr.aria-expanded]="expanded()" (click)="expanded.set(!expanded())">
        {{ expanded() ? 'less' : 'more' }}<span class="visually-hidden"> of this text</span>
      </button>
    }
  `,
  styles: [`
    /* relative: keeps the visually-hidden button label inside scrolling containers (e.g. the matrix). */
    :host { display: block; position: relative; }
    .text { display: block; overflow-wrap: anywhere; }
    .clamped {
      display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
      -webkit-line-clamp: var(--fr-clamp-lines, 3); line-clamp: var(--fr-clamp-lines, 3);
    }
    .toggle {
      margin-top: 2px; padding: 2px 0; min-height: 24px; border: 0; background: none; cursor: pointer;
      font: inherit; font-size: var(--fr-text-small); font-weight: 600; color: var(--fr-accent);
    }
    .toggle:hover { text-decoration: underline; }
    /* In-app links take the surrounding text style (e.g. a source title), underlined on hover. */
    .internal { color: inherit; text-decoration: none; }
    .internal:hover { color: var(--fr-accent); text-decoration: underline; }
  `],
})
export class ClampTextComponent {
  readonly text = input.required<string>();
  readonly lines = input(3);
  /** Optional: render the text as an external link. */
  readonly href = input<string | null>(null);
  /** Optional: render the text as a link inside the app (router path). */
  readonly route = input<string | null>(null);

  protected readonly expanded = signal(false);
  protected readonly overflows = signal(false);
  private readonly box = viewChild<ElementRef<HTMLElement>>('box');

  constructor() {
    // The text element sits inside @if (link or plain text): attach once it exists, re-measure on resize.
    effect((onCleanup) => {
      const el = this.box()?.nativeElement;
      if (!el) return;
      const measure = () => {
        if (!this.expanded()) this.overflows.set(el.scrollHeight > el.clientHeight + 1);
      };
      if (typeof ResizeObserver === 'undefined') {
        requestAnimationFrame(measure);
        return;
      }
      const ro = new ResizeObserver(measure); // fires once after layout, then on every resize
      ro.observe(el);
      onCleanup(() => ro.disconnect());
    });
  }
}
