import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { HealthResponse, HealthService } from './health.service';

@Component({
  selector: 'app-health',
  standalone: true,
  imports: [MatButtonModule, MatCardModule, MatChipsModule, MatProgressSpinnerModule],
  templateUrl: './health.component.html',
  styleUrl: './health.component.scss',
})
export class HealthComponent implements OnInit {
  private readonly healthService = inject(HealthService);

  readonly loading = signal(true);
  readonly health = signal<HealthResponse | null>(null);
  readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.error.set(null);
    this.healthService.check().subscribe({
      next: (res) => {
        this.health.set(res);
        this.loading.set(false);
      },
      error: (err: HttpErrorResponse) => {
        // A 503 still carries a health body (db disconnected).
        if (err.status === 503 && err.error?.db) {
          this.health.set(err.error as HealthResponse);
        } else {
          this.health.set(null);
          this.error.set('Backend unreachable');
        }
        this.loading.set(false);
      },
    });
  }
}
