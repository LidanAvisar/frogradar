import { Injectable, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { catchError, map, of } from 'rxjs';

import { ApiService } from './api.service';
import { HighlightTerms, NO_TERMS } from './highlight';

/** JFrog product names to highlight (from config via /api/self). Empty until loaded or on error. */
@Injectable({ providedIn: 'root' })
export class JfrogTermsService {
  readonly terms = toSignal(
    inject(ApiService)
      .self()
      .pipe(
        map((s): HighlightTerms => ({ always: s.product_terms, guarded: s.bare_terms })),
        catchError(() => of(NO_TERMS)),
      ),
    { initialValue: NO_TERMS },
  );
}
