import { Pipe, PipeTransform } from '@angular/core';

import { HighlightTerms, Segment, highlightSegments } from './highlight';

/** `text | highlight: terms` → segments; render each as text, wrapping hits in <strong>. */
@Pipe({ name: 'highlight', standalone: true })
export class HighlightPipe implements PipeTransform {
  transform(text: string | null | undefined, terms: HighlightTerms): Segment[] {
    return highlightSegments(text, terms);
  }
}
