import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import type { MissionDetail, MissionFinding, RadarRefreshOut } from '../../api/models';
import { ApiService } from '../../core/api.service';
import { dateTime, plural, relationInfo } from '../../core/format';
import { PreferencesService } from '../../core/preferences.service';
import { ErrorStateComponent, EmptyStateComponent, SkeletonComponent } from '../../shared/states.component';
import { VendorClaimTagComponent } from '../../shared/vendor-claim-tag.component';
import { isLive, missionOriginLabel, missionStatusInfo, usd } from './mission-format';

export const POLL_MS = 2000;

type State = { status: 'loading' } | { status: 'error'; notFound: boolean } | { status: 'ready' };
type PlanRow = { question: string; search_query: string; why: string };
type QState = 'done' | 'active' | 'pending' | 'skipped';

/** One mission: plan (approve / edit while awaiting), live step log, budget and steps, findings to review, report. */
@Component({
  selector: 'fr-mission-detail-page',
  standalone: true,
  imports: [EmptyStateComponent, ErrorStateComponent, FormsModule, RouterLink, SkeletonComponent, VendorClaimTagComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './mission-detail-page.component.html',
  styleUrl: './mission-detail-page.component.scss',
})
export class MissionDetailPageComponent {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly prefs = inject(PreferencesService);

  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly m = signal<MissionDetail | null>(null);
  protected readonly announcement = signal('');
  protected readonly planDraft = signal<PlanRow[]>([]);
  protected readonly planBusy = signal(false);
  protected readonly actionError = signal('');
  protected readonly busyFinding = signal<number | null>(null);
  protected readonly radarRefresh = signal<RadarRefreshOut | null>(null);
  protected readonly radarBusy = signal(false);

  private id = NaN;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private lastStatus = '';

  protected readonly status = computed(() => missionStatusInfo(this.m()?.status ?? ''));
  protected readonly origin = missionOriginLabel;
  protected readonly usd = usd;
  protected readonly when = dateTime;
  protected readonly live = computed(() => isLive(this.m()?.status ?? ''));
  protected readonly awaiting = computed(() => this.m()?.status === 'awaiting_approval');
  protected readonly stoppable = computed(() =>
    ['queued', 'planning', 'awaiting_approval', 'running'].includes(this.m()?.status ?? '') && !this.m()?.stop_requested,
  );
  protected readonly budgetPct = computed(() => {
    const m = this.m();
    return m && m.budget_usd > 0 ? Math.min(100, Math.round((m.cost_usd / m.budget_usd) * 100)) : 0;
  });
  protected readonly findingsNew = computed(() => (this.m()?.findings ?? []).filter((f) => f.status === 'new').length);
  protected readonly stepsPlanned = computed(() => 4 * (this.m()?.plan?.sub_questions.length ?? 0));
  protected readonly draftSteps = computed(() => 4 * this.planDraft().filter((q) => q.question.trim().length >= 5).length);
  protected readonly logSteps = computed(() => [...(this.m()?.steps ?? [])].reverse());
  protected readonly approvedCount = computed(() => (this.m()?.findings ?? []).filter((f) => f.status === 'approved').length);

  /** Plan rows with progress derived from the step log ("Q2: …" summaries). */
  protected readonly planRows = computed(() => {
    const m = this.m();
    if (!m?.plan) return [];
    const finished = !isLive(m.status) && m.status !== 'awaiting_approval';
    return m.plan.sub_questions.map((q, i) => {
      const tag = `Q${i + 1}:`;
      const steps = m.steps.filter((s) => s.summary.startsWith(tag));
      const verified = steps.some((s) => s.kind === 'verify');
      const st: QState = verified ? 'done' : steps.length ? (finished ? 'skipped' : 'active') : finished ? 'skipped' : 'pending';
      const last = steps.at(-1)?.summary.replace(tag, '').trim() ?? '';
      return { n: i + 1, question: q.question, why: q.why, state: st, detail: last };
    });
  });
  protected readonly reportState = computed<QState>(() => {
    const m = this.m();
    if (!m) return 'pending';
    if (m.steps.some((s) => s.kind === 'report')) return 'done';
    return 'pending';
  });

  protected readonly dropped = computed(() => {
    const counts = new Map<string, number>();
    for (const r of this.m()?.dropped ?? []) {
      const key = r.startsWith('verifier: unsupported') ? 'Verifier: not supported by the cited source' : r;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].map(([reason, n]) => ({ reason, n }));
  });

  protected readonly reportPoints = computed(() => {
    const m = this.m();
    if (!m?.report) return [];
    const index = new Map(m.findings.map((f, i) => [f.id, i + 1]));
    return m.report.summary.map((p) => ({ text: p.text, refs: p.finding_ids.filter((id) => index.has(id)).map((id) => ({ id, n: index.get(id)! })) }));
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    destroyRef.onDestroy(() => this.clearTimer());
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((p) => {
      this.id = Number(p.get('id'));
      this.clearTimer();
      this.lastStatus = '';
      this.state.set({ status: 'loading' });
      this.load();
    });
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  load(): void {
    const id = this.id;
    this.api.mission(id).subscribe({
      next: (m) => {
        if (id !== this.id) return;
        this.apply(m);
        this.state.set({ status: 'ready' });
        this.clearTimer();
        if (isLive(m.status)) this.timer = setTimeout(() => this.load(), POLL_MS);
      },
      error: (e) => {
        if (id !== this.id) return;
        if (this.m()) {
          // Keep showing the last state; try again on the next tick while the mission is live.
          this.clearTimer();
          if (this.live()) this.timer = setTimeout(() => this.load(), POLL_MS * 2);
          return;
        }
        this.state.set({ status: 'error', notFound: e?.status === 404 });
      },
    });
  }

  private apply(m: MissionDetail): void {
    if (this.lastStatus && this.lastStatus !== m.status) {
      this.announcement.set(`Mission status: ${missionStatusInfo(m.status).label}.`);
    }
    if (m.status === 'awaiting_approval' && this.lastStatus !== 'awaiting_approval') {
      this.planDraft.set((m.plan?.sub_questions ?? []).map((q) => ({ question: q.question, search_query: q.search_query, why: q.why ?? '' })));
    }
    this.lastStatus = m.status;
    this.m.set(m);
  }

  retry(): void {
    this.state.set({ status: 'loading' });
    this.load();
  }

  // ---------- plan ----------

  editQuestion(i: number, text: string): void {
    this.planDraft.update((rows) => rows.map((r, j) => (j === i ? { ...r, question: text } : r)));
  }

  removeQuestion(i: number): void {
    this.planDraft.update((rows) => rows.filter((_, j) => j !== i));
  }

  addQuestion(): void {
    this.planDraft.update((rows) => (rows.length >= 6 ? rows : [...rows, { question: '', search_query: '', why: '' }]));
  }

  approvePlan(): void {
    const m = this.m();
    if (!m || this.planBusy()) return;
    const rows = this.planDraft().filter((r) => r.question.trim().length >= 5);
    if (!rows.length) {
      this.actionError.set('Keep at least one sub-question (5 characters or more).');
      return;
    }
    const original = m.plan?.sub_questions ?? [];
    const edited =
      rows.length !== original.length || rows.some((r, i) => r.question.trim() !== original[i]?.question);
    this.planBusy.set(true);
    this.actionError.set('');
    this.api
      .approvePlan(m.id, {
        analyst: this.prefs.analystName(),
        // An unchanged question keeps the planner's search query; an edited one is searched as written.
        sub_questions: edited
          ? rows.map((r) => {
              const same = original.find((o) => o.question === r.question.trim());
              return { question: r.question.trim(), search_query: same?.search_query ?? '', why: same?.why ?? '' };
            })
          : null,
      })
      .subscribe({
        next: (d) => {
          this.planBusy.set(false);
          this.apply(d);
          this.announcement.set('Plan approved. Research has started.');
          this.clearTimer();
          this.timer = setTimeout(() => this.load(), POLL_MS);
        },
        error: (e) => {
          this.planBusy.set(false);
          this.actionError.set(typeof e?.error?.detail === 'string' ? e.error.detail : 'The plan could not be approved. Try again.');
        },
      });
  }

  stop(): void {
    const m = this.m();
    if (!m) return;
    this.api.stopMission(m.id).subscribe({
      next: (d) => {
        this.apply(d);
        this.announcement.set(d.status === 'cancelled' ? 'Mission stopped.' : 'Stopping after the current step…');
      },
      error: () => this.actionError.set('The mission could not be stopped. Try again.'),
    });
  }

  // ---------- findings ----------

  review(f: MissionFinding, action: 'approve' | 'reject'): void {
    if (this.busyFinding() !== null) return;
    const before = this.m();
    const patch = (status: MissionFinding['status'], signalId: number | null = f.signal_id) =>
      this.m.update((m) => (m ? { ...m, findings: m.findings.map((x) => (x.id === f.id ? { ...x, status, signal_id: signalId } : x)) } : m));
    patch(action === 'approve' ? 'approved' : 'rejected'); // optimistic
    this.busyFinding.set(f.id);
    this.actionError.set('');
    const body = { analyst: this.prefs.analystName() };
    const call = action === 'approve' ? this.api.approveFinding(f.id, body) : this.api.rejectFinding(f.id, body);
    call.subscribe({
      next: (r) => {
        this.busyFinding.set(null);
        patch(r.finding.status, r.finding.signal_id);
        this.announcement.set(action === 'approve' ? 'Finding approved: it is now a signal.' : 'Finding rejected.');
      },
      error: () => {
        this.busyFinding.set(null);
        this.m.set(before);
        this.actionError.set(`The finding could not be ${action === 'approve' ? 'approved' : 'rejected'}. Try again.`);
      },
    });
  }

  refreshRadar(): void {
    const m = this.m();
    if (!m || this.radarBusy()) return;
    this.radarBusy.set(true);
    this.actionError.set('');
    this.api.refreshRadar(m.id).subscribe({
      next: (r) => {
        this.radarBusy.set(false);
        this.radarRefresh.set(r);
        this.announcement.set('Radar profile refreshed.');
      },
      error: (e) => {
        this.radarBusy.set(false);
        this.actionError.set(typeof e?.error?.detail === 'string' ? e.error.detail : 'The radar profile could not be refreshed.');
      },
    });
  }

  relationLabel(rel: string | null | undefined): string {
    return rel ? relationInfo(rel).label : 'No trusted relation';
  }

  plural = plural;
}
