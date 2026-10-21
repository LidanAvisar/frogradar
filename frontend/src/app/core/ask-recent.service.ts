import { Injectable, inject, signal } from '@angular/core';

import type { AskConversation } from '../api/models';
import { ApiService } from './api.service';

/** Recent Ask conversations for the sidebar. Refreshed after each new answer. */
@Injectable({ providedIn: 'root' })
export class AskRecentService {
  private readonly api = inject(ApiService);
  readonly items = signal<AskConversation[]>([]);

  refresh(): void {
    this.api.askConversations(5).subscribe({
      next: (list) => this.items.set(list),
      error: () => undefined, // keep the last list
    });
  }
}
