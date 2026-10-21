import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type {
  AskConversation,
  AskConversationDetail,
  AskFeedbackIn,
  AskIn,
  AskOut,
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
  ReviewQueue,
  Scorecard,
  SelfInfo,
  Signal,
  SignalApproveIn,
  SignalCorrectIn,
  SignalPage,
  SignalRejectIn,
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

  approveSignal(id: number, body: SignalApproveIn): Observable<Signal> {
    return this.http.post<Signal>(`/api/signals/${id}/approve`, body);
  }

  correctSignal(id: number, body: SignalCorrectIn): Observable<Signal> {
    return this.http.post<Signal>(`/api/signals/${id}/correct`, body);
  }

  rejectSignal(id: number, body: SignalRejectIn): Observable<Signal> {
    return this.http.post<Signal>(`/api/signals/${id}/reject`, body);
  }

  reviewQueue(limit = 100): Observable<ReviewQueue> {
    return this.http.get<ReviewQueue>('/api/review', { params: { limit } });
  }

  scorecard(): Observable<Scorecard> {
    return this.http.get<Scorecard>('/api/feedback/scorecard');
  }

  ask(body: AskIn): Observable<AskOut> {
    return this.http.post<AskOut>('/api/ask', body);
  }

  askConversations(limit = 5): Observable<AskConversation[]> {
    return this.http.get<AskConversation[]>('/api/ask/conversations', { params: { limit } });
  }

  askConversation(id: number): Observable<AskConversationDetail> {
    return this.http.get<AskConversationDetail>(`/api/ask/conversations/${id}`);
  }

  askFeedback(turnId: number, body: AskFeedbackIn): Observable<unknown> {
    return this.http.post(`/api/ask/turns/${turnId}/feedback`, body);
  }
}
