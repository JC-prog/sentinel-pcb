import { Component, OnDestroy, OnInit, computed, signal } from '@angular/core';
import { AuthService } from '../auth.service';
import { ModelOpsService } from './model-ops.service';
import {
  DriftReport,
  FINISHED_JOB_STATUSES,
  JobStatus,
  ModelSummary,
  ModelVersion,
  ModelVersionStatus,
  RetrainingJob,
  RetrainingJobDetail,
} from './model-ops.models';

const BADGE = 'rounded-full px-2 py-0.5 text-xs font-medium';

const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  pending_approval: 'Pending approval',
  approved: 'Approved – not sent',
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const JOB_STATUS_CLASSES: Record<JobStatus, string> = {
  pending_approval: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  approved: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  queued: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200',
  running: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200',
  succeeded: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200',
  cancelled: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
};

const VERSION_STATUS_CLASSES: Record<ModelVersionStatus, string> = {
  live: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200',
  previous: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  candidate: 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-200',
  retired: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
};

type JobDetailState = RetrainingJobDetail | 'loading' | 'error';

/** The Models tab: which version of each model is live and its history, drift reports, and the
 * retraining queue. Everyone logged in can read it; the buttons that spend compute or change what
 * is live (approve, promote, roll back, resolve, cancel) are shown to Admins only - the backend
 * enforces that regardless (app/modelops/api/models.py), this only avoids offering what would be
 * refused. Never reachable from the chat tools: chat can draft a plan, an Admin acts on it here. */
@Component({
  selector: 'app-models',
  styleUrl: './models.css',
  templateUrl: './models.html',
})
export class Models implements OnInit, OnDestroy {
  protected readonly driftWindows = [7, 30, 90];
  protected readonly driftDays = signal(30);
  /** Models whose version history is expanded. */
  protected readonly openHistory = signal<ReadonlySet<string>>(new Set());
  /** Expanded retraining jobs -> their loaded detail (the flagged samples). */
  protected readonly jobDetails = signal<Record<string, JobDetailState>>({});

  protected readonly isAdmin = computed(() => this.authService.currentUser()?.role === 'admin');

  constructor(
    protected readonly service: ModelOpsService,
    private readonly authService: AuthService,
  ) {}

  ngOnInit(): void {
    void this.service.refresh();
    this.service.startPolling();
  }

  ngOnDestroy(): void {
    this.service.stopPolling();
  }

  // ------------------------------------------------------------------------------- display

  protected jobStatusLabel(status: JobStatus): string {
    return JOB_STATUS_LABELS[status];
  }

  protected jobStatusClass(status: JobStatus): string {
    return `${BADGE} ${JOB_STATUS_CLASSES[status]}`;
  }

  protected versionStatusClass(status: ModelVersionStatus): string {
    return `${BADGE} ${VERSION_STATUS_CLASSES[status]}`;
  }

  protected formatDate(iso: string | null): string {
    return iso ? new Date(iso).toLocaleString() : '–';
  }

  protected percent(value: number | null | undefined): string {
    return value === null || value === undefined ? '–' : `${Math.round(value * 100)}%`;
  }

  protected progressPercent(job: RetrainingJob): number {
    return Math.round(job.progress * 100);
  }

  protected showsProgress(job: RetrainingJob): boolean {
    return job.status === 'queued' || job.status === 'running';
  }

  protected isFinished(job: RetrainingJob): boolean {
    return FINISHED_JOB_STATUSES.includes(job.status);
  }

  /** "override rate 40%" style one-liners from a report's snapshot, for the numbers worth a glance. */
  protected driftFigures(report: DriftReport): string[] {
    const recent = report.stats.recent;
    if (!recent || recent.cases === 0) {
      return [];
    }
    const figures = [`${recent.cases} cases in window`];
    if (recent.override_rate !== null) {
      figures.push(`override rate ${this.percent(recent.override_rate)}`);
    }
    if (recent.review_rate !== null) {
      figures.push(`review rate ${this.percent(recent.review_rate)}`);
    }
    if (recent.mean_confidence !== null) {
      figures.push(`mean confidence ${recent.mean_confidence.toFixed(2)}`);
    }
    return figures;
  }

  protected driftEntries(counts: Record<string, number> | undefined): [string, number][] {
    return Object.entries(counts ?? {});
  }

  protected detailOf(job: RetrainingJob): RetrainingJobDetail | null {
    const state = this.jobDetails()[job.id];
    return state && state !== 'loading' && state !== 'error' ? state : null;
  }

  protected detailState(job: RetrainingJob): 'loading' | 'error' | null {
    const state = this.jobDetails()[job.id];
    return state === 'loading' || state === 'error' ? state : null;
  }

  protected isJobOpen(job: RetrainingJob): boolean {
    return job.id in this.jobDetails();
  }

  protected isHistoryOpen(model: ModelSummary): boolean {
    return this.openHistory().has(model.name);
  }

  // ------------------------------------------------------------------------ who can do what

  protected canApprove(job: RetrainingJob): boolean {
    return this.isAdmin() && job.status === 'pending_approval';
  }

  protected canResend(job: RetrainingJob): boolean {
    return this.isAdmin() && job.status === 'approved';
  }

  /** An Admin can cancel any unfinished job; whoever drafted a plan can withdraw it while it is
   * still awaiting approval - the same rule the backend applies. */
  protected canCancel(job: RetrainingJob): boolean {
    if (this.isFinished(job)) {
      return false;
    }
    if (this.isAdmin()) {
      return true;
    }
    return (
      job.status === 'pending_approval' &&
      job.created_by === this.authService.currentUser()?.username
    );
  }

  protected cancelLabel(job: RetrainingJob): string {
    return this.isAdmin() || job.status !== 'pending_approval' ? 'Cancel' : 'Withdraw';
  }

  protected canPromote(version: ModelVersion): boolean {
    return this.isAdmin() && version.status !== 'live';
  }

  // ------------------------------------------------------------------------------ actions

  protected setDriftWindow(days: number): void {
    this.driftDays.set(days);
    void this.service.setDriftWindow(days);
  }

  protected toggleHistory(model: ModelSummary): void {
    this.openHistory.update((open) => {
      const next = new Set(open);
      if (!next.delete(model.name)) {
        next.add(model.name);
      }
      return next;
    });
  }

  protected async toggleJob(job: RetrainingJob): Promise<void> {
    if (this.isJobOpen(job)) {
      this.jobDetails.update(({ [job.id]: _closed, ...rest }) => rest);
      return;
    }
    this.jobDetails.update((details) => ({ ...details, [job.id]: 'loading' }));
    try {
      const detail = await this.service.loadJob(job.id);
      this.jobDetails.update((details) => ({ ...details, [job.id]: detail }));
    } catch {
      this.jobDetails.update((details) => ({ ...details, [job.id]: 'error' }));
    }
  }

  protected async approve(job: RetrainingJob): Promise<void> {
    const cases = `${job.sample_count} flagged case${job.sample_count === 1 ? '' : 's'}`;
    if (
      this.confirm(
        `Approve retraining ${job.model_name} on ${cases}? It will be sent to the inference service.`,
      )
    ) {
      await this.service.approveJob(job.id);
      await this.reloadDetailIfOpen(job);
    }
  }

  protected async resend(job: RetrainingJob): Promise<void> {
    await this.service.submitJob(job.id);
    await this.reloadDetailIfOpen(job);
  }

  protected async cancel(job: RetrainingJob): Promise<void> {
    if (this.confirm(`${this.cancelLabel(job)} this retraining plan for ${job.model_name}?`)) {
      await this.service.cancelJob(job.id);
      await this.reloadDetailIfOpen(job);
    }
  }

  protected async resolve(report: DriftReport): Promise<void> {
    await this.service.resolveDrift(report.id);
  }

  protected async promote(model: ModelSummary, version: ModelVersion): Promise<void> {
    if (
      this.confirm(
        `Make ${version.version} the live ${model.name} model? The inference service will load it ` +
          `and swap it in; ${model.live?.version ?? 'the current model'} stays available to roll back to.`,
      )
    ) {
      await this.service.promote(model.name, version.version);
    }
  }

  protected async rollback(model: ModelSummary): Promise<void> {
    if (!model.previous) {
      return;
    }
    if (this.confirm(`Roll ${model.name} back to ${model.previous.version}?`)) {
      await this.service.rollback(model.name);
    }
  }

  protected dismissActionError(): void {
    this.service.clearActionError();
  }

  protected confirm(message: string): boolean {
    return window.confirm(message);
  }

  /** Keeps an expanded job's sample list in step with an action taken on it. */
  private async reloadDetailIfOpen(job: RetrainingJob): Promise<void> {
    if (this.isJobOpen(job)) {
      this.jobDetails.update(({ [job.id]: _stale, ...rest }) => rest);
      await this.toggleJob(job);
    }
  }
}
