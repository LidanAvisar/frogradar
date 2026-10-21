import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type {
  Battlecard,
  BattlecardListItem,
  Brief,
  Entity,
  NavCounts,
  RadarApproveIn,
  RadarApproveOut,
  RadarCandidateDetail,
  RadarDismissIn,
  RadarList,
  SelfInfo,
  Signal,
  SignalPage,
} from '../api/models';
import type { operations } from '../api/schema';

export type SignalQuery = NonNullable<operations['list_signals_api_signals_get']['parameters']['query']>;
export type RadarQuery = NonNullable<operations['list_candidates_api_radar_get']['parameters']['query']>;

function toParams(query: object): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== '') {
      params = params.set(key, String(value));
    }
  }
  return params;
}

/** API client. Shapes come from the generated OpenAPI types. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  brief(days = 7, limit = 4): Observable<Brief> {
    return this.http.get<Brief>('/api/brief', { params: { days, limit } });
  }

  signals(query: SignalQuery = {}): Observable<SignalPage> {
    return this.http.get<SignalPage>('/api/signals', { params: toParams(query) });
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

  battlecards(): Observable<BattlecardListItem[]> {
    return this.http.get<BattlecardListItem[]>('/api/battlecards');
  }

  battlecard(slug: string): Observable<Battlecard> {
    return this.http.get<Battlecard>(`/api/entities/${encodeURIComponent(slug)}/battlecard`);
  }

  radar(query: RadarQuery = {}): Observable<RadarList> {
    return this.http.get<RadarList>('/api/radar', { params: toParams(query) });
  }

  radarCandidate(id: number): Observable<RadarCandidateDetail> {
    return this.http.get<RadarCandidateDetail>(`/api/radar/${id}`);
  }

  watchCandidate(id: number): Observable<RadarCandidateDetail> {
    return this.http.post<RadarCandidateDetail>(`/api/radar/${id}/watch`, {});
  }

  approveCandidate(id: number, body: RadarApproveIn): Observable<RadarApproveOut> {
    return this.http.post<RadarApproveOut>(`/api/radar/${id}/approve`, body);
  }

  dismissCandidate(id: number, body: RadarDismissIn): Observable<RadarCandidateDetail> {
    return this.http.post<RadarCandidateDetail>(`/api/radar/${id}/dismiss`, body);
  }
}
