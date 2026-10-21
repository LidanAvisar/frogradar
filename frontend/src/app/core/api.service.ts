import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type { Brief, Entity, NavCounts, SelfInfo, Signal, SignalPage } from '../api/models';
import type { operations } from '../api/schema';

export type SignalQuery = NonNullable<operations['list_signals_api_signals_get']['parameters']['query']>;

/** Read API client. Shapes come from the generated OpenAPI types. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  brief(days = 7, limit = 5): Observable<Brief> {
    return this.http.get<Brief>('/api/brief', { params: { days, limit } });
  }

  signals(query: SignalQuery = {}): Observable<SignalPage> {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    }
    return this.http.get<SignalPage>('/api/signals', { params });
  }

  signal(id: number): Observable<Signal> {
    return this.http.get<Signal>(`/api/signals/${id}`);
  }

  entities(): Observable<Entity[]> {
    return this.http.get<Entity[]>('/api/entities');
  }

  self(): Observable<SelfInfo> {
    return this.http.get<SelfInfo>('/api/self');
  }

  nav(): Observable<NavCounts> {
    return this.http.get<NavCounts>('/api/nav');
  }
}
