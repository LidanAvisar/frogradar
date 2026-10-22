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
  FindingReviewIn,
  FindingReviewOut,
  MissionCreateIn,
  MissionDetail,
  MissionList,
  MissionPlanIn,
  MissionPrefill,
  NavCounts,
  RadarApproveIn,
  RadarApproveOut,
  RadarCandidateDetail,
  RadarDismissIn,
  RadarList,
  RadarRefreshOut,
  ReviewQueue,
  Scorecard,
  SelfInfo,
  Signal,
  SignalApproveIn,
  SignalCorrectIn,
  SignalPage,
  SignalRejectIn,
  SystemStatus,
} from '../api/models';
import type { operations } from '../api/schema';

export type SignalQuery = NonNullable<operations['list_signals_api_signals_get']['parameters']['query']>;
export type PrefillQuery = NonNullable<operations['get_prefill_api_missions_prefill_get']['parameters']['query']>;
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

  status(): Observable<SystemStatus> {
    return this.http.get<SystemStatus>('/api/status');
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

  missionPrefill(query: PrefillQuery): Observable<MissionPrefill> {
    return this.http.get<MissionPrefill>('/api/missions/prefill', { params: toParams(query) });
  }

  missions(limit = 50): Observable<MissionList> {
    return this.http.get<MissionList>('/api/missions', { params: { limit } });
  }

  mission(id: number): Observable<MissionDetail> {
    return this.http.get<MissionDetail>(`/api/missions/${id}`);
  }

  createMission(body: MissionCreateIn): Observable<MissionDetail> {
    return this.http.post<MissionDetail>('/api/missions', body);
  }

  approvePlan(id: number, body: MissionPlanIn): Observable<MissionDetail> {
    return this.http.post<MissionDetail>(`/api/missions/${id}/plan`, body);
  }

  stopMission(id: number): Observable<MissionDetail> {
    return this.http.post<MissionDetail>(`/api/missions/${id}/stop`, {});
  }

  approveFinding(id: number, body: FindingReviewIn): Observable<FindingReviewOut> {
    return this.http.post<FindingReviewOut>(`/api/missions/findings/${id}/approve`, body);
  }

  rejectFinding(id: number, body: FindingReviewIn): Observable<FindingReviewOut> {
    return this.http.post<FindingReviewOut>(`/api/missions/findings/${id}/reject`, body);
  }

  refreshRadar(id: number): Observable<RadarRefreshOut> {
    return this.http.post<RadarRefreshOut>(`/api/missions/${id}/refresh-radar`, {});
  }
}
