import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { catchError, map, of } from 'rxjs';

import type { Entity, Mission, MissionCreateIn, MissionPrefill } from '../../api/models';
import { ApiService, type PrefillQuery } from '../../core/api.service';
import { errorMessage } from '../../core/api-error';
import { dateTime } from '../../core/format';
import { PreferencesService } from '../../core/preferences.service';
import { StatusService, usdShort } from '../../core/status.service';
import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from '../../shared/states.component';
import { missionOriginLabel, missionStatusInfo, usd } from './mission-format';

type ListState = { status: 'loading' } | { status: 'error' } | { status: 'ready'; items: Mission[] };

const PREFILL_IDS = ['signal', 'candidate', 'entity', 'capability', 'turn'] as const;
const PREFILL_SLUGS = ['entity_slug', 'capability_slug'] as const;

/** Missions: the list (status, origin, cost) and the "New mission" form, pre-filled from an origin. */
@Component({
  selector: 'fr-missions-page',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, FormsModule, RouterLink, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './missions-page.component.html',
  styleUrl: './missions-page.component.scss',
})
export class MissionsPageComponent {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly prefs = inject(PreferencesService);
  private readonly statusSvc = inject(StatusService);
  protected readonly demo = this.statusSvc.demo;
  protected readonly missionCost = computed(() => usdShort(this.statusSvc.costs()?.mission));

  protected readonly list = signal<ListState>({ status: 'loading' });
  protected readonly formOpen = signal(false);
  protected readonly prefill = signal<MissionPrefill | null>(null);
  protected readonly prefillError = signal('');
  protected readonly submitting = signal(false);
  protected readonly submitError = signal('');

  // Form fields
  protected readonly brief = signal('');
  protected readonly companyIds = signal<number[]>([]);
  protected readonly days = signal<number | null>(null);
  protected readonly budget = signal(0.3);
  protected readonly maxSteps = signal(12);

  protected readonly companies = toSignal(
    this.api.entities().pipe(
      map((es) => es.filter((e: Entity) => e.type === 'company' && !e.is_self)),
      catchError(() => of([] as Entity[])),
    ),
    { initialValue: [] as Entity[] },
  );

  private readonly briefField = viewChild<ElementRef<HTMLTextAreaElement>>('briefField');

  protected readonly windows = [
    { value: null, label: 'Any time' },
    { value: 30, label: 'Last 30 days' },
    { value: 90, label: 'Last 90 days' },
    { value: 180, label: 'Last 6 months' },
    { value: 365, label: 'Last 12 months' },
  ];

  protected readonly status = missionStatusInfo;
  protected readonly origin = missionOriginLabel;
  protected readonly usd = usd;
  protected readonly when = dateTime;

  protected readonly canSubmit = computed(() => this.brief().trim().length >= 10 && !this.submitting());

  constructor() {
    this.load();
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((q) => {
      const query: PrefillQuery = {};
      for (const k of PREFILL_IDS) {
        const v = q.get(k);
        if (v && Number.isFinite(Number(v))) query[k] = Number(v);
      }
      for (const k of PREFILL_SLUGS) {
        const v = q.get(k);
        if (v) query[k] = v;
      }
      if (q.get('new') === '1' || Object.keys(query).length) this.openForm(query);
    });
  }

  load(): void {
    this.list.set({ status: 'loading' });
    this.api.missions().subscribe({
      next: (r) => this.list.set({ status: 'ready', items: r.items }),
      error: () => this.list.set({ status: 'error' }),
    });
  }

  openForm(query: PrefillQuery = {}): void {
    this.formOpen.set(true);
    this.prefillError.set('');
    this.submitError.set('');
    this.api.missionPrefill(query).subscribe({
      next: (p) => {
        this.prefill.set(p);
        this.brief.set(p.brief);
        this.companyIds.set(p.company_ids);
        this.days.set(p.days ?? null);
        this.budget.set(p.budget_usd);
        this.maxSteps.set(p.max_steps);
        setTimeout(() => this.briefField()?.nativeElement.focus());
      },
      error: () => this.prefillError.set('Could not load the starting point. You can still write a brief.'),
    });
  }

  closeForm(): void {
    this.formOpen.set(false);
    this.prefill.set(null);
    void this.router.navigate([], { queryParams: {} });
  }

  toggleCompany(id: number, on: boolean): void {
    this.companyIds.update((ids) => (on ? [...new Set([...ids, id])] : ids.filter((x) => x !== id)));
  }

  submit(): void {
    if (!this.canSubmit()) return;
    const p = this.prefill();
    const body: MissionCreateIn = {
      brief: this.brief().trim(),
      scope: { company_ids: this.companyIds(), days: this.days() },
      budget_usd: this.budget(),
      max_steps: this.maxSteps(),
      origin_kind: p?.origin_kind ?? 'manual',
      origin_ref: p?.origin_ref ?? {},
      analyst: this.prefs.analystName(),
    };
    this.submitting.set(true);
    this.submitError.set('');
    this.api.createMission(body).subscribe({
      next: (m) => {
        this.submitting.set(false);
        void this.router.navigate(['/missions', m.id]);
      },
      error: (e) => {
        this.submitting.set(false);
        this.submitError.set(errorMessage(e, 'The mission could not be created. Check the fields and try again.'));
      },
    });
  }
}
