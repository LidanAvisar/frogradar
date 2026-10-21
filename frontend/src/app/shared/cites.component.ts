import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { CitationRef } from '../api/models';

export interface Cite {
  n: number;
  ref: CitationRef;
}

/** Numbered links to the source signals of a sentence: ¹ ² (each opens the original). */
@Component({
  selector: 'fr-cites',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@for (c of cites(); track c.ref.id) {<sup class="fr-cite"><a [href]="c.ref.url" target="_blank" rel="noopener noreferrer" [attr.title]="c.ref.title" [attr.aria-label]="'Source ' + c.n + ': ' + c.ref.title">{{ c.n }}</a></sup>}`,
  styles: [':host { display: inline; }'],
})
export class CitesComponent {
  readonly cites = input.required<Cite[]>();
}

/** Numbers citations in order of first use. */
export class CitationNumbers {
  private readonly order = new Map<number, number>();

  number(ref: CitationRef): Cite {
    if (!this.order.has(ref.id)) this.order.set(ref.id, this.order.size + 1);
    return { n: this.order.get(ref.id)!, ref };
  }

  all(refs: CitationRef[]): Cite[] {
    return refs.map((r) => this.number(r));
  }
}
