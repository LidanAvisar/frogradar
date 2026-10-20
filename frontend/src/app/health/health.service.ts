import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

// Hand-written for now; will be generated from the FastAPI OpenAPI schema later.
export interface HealthResponse {
  status: 'ok' | 'degraded';
  app: string;
  db: 'connected' | 'disconnected';
}

@Injectable({ providedIn: 'root' })
export class HealthService {
  private readonly http = inject(HttpClient);

  check(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>('/health');
  }
}
