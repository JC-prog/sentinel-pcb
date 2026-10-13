import { Injectable, Signal, computed, signal } from '@angular/core';
import { ModelOpsClient } from './model-ops-client';
import {
  ACTIVE_JOB_STATUSES,
  DriftOverview,
  ModelsOverview,
  QueueOverview,
  RetrainingJobDetail,
} from './model-ops.models';

const POLL_INTERVAL_MS = 5000;

/** State for the Models tab. Loads the three views (models, drift, retraining queue) together,
 * runs the Admin actions, and - while any retraining job is still moving - polls so progress and
 * completion show up without a manual refresh. Failures never throw out of here: a failed load
 * leaves the last good data on screen with an error banner, a failed action sets `actionError`. */
@Injectable({ providedIn: 'root' })
export class ModelOpsService {
  private readonly _overview = signal<ModelsOverview | null>(null);
  private readonly _drift = signal<DriftOverview | null>(null);
  private readonly _queue = signal<QueueOverview | null>(null);
  private readonly _loading = signal(false);
  private readonly _loadError = signal<string | null>(null);
  private readonly _actionError = signal<string | null>(null);
  private readonly _busy = signal<ReadonlySet<string>>(new Set());
  private readonly _lastUpdated = signal<Date | null>(null);

  private driftDays = 30;
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  readonly overview: Signal<ModelsOverview | null> = this._overview.asReadonly();
  readonly drift: Signal<DriftOverview | null> = this._drift.asReadonly();
  readonly queue: Signal<QueueOverview | null> = this._queue.asReadonly();
  readonly loading: Signal<boolean> = this._loading.asReadonly();
  readonly loadError: Signal<string | null> = this._loadError.asReadonly();
  readonly actionError: Signal<string | null> = this._actionError.asReadonly();
  readonly lastUpdated: Signal<Date | null> = this._lastUpdated.asReadonly();

  /** Whether the numbers reflect the inference service right now (null until first load). */
  readonly inference = computed(() => this._overview()?.inference ?? null);

  readonly hasActiveJobs = computed(
    () => this._queue()?.jobs.some((job) => ACTIVE_JOB_STATUSES.includes(job.status)) ?? false,
  );

  constructor(private readonly client: ModelOpsClient) {}

  isBusy(key: string): boolean {
    return this._busy().has(key);
  }

  clearActionError(): void {
    this._actionError.set(null);
  }

  /** Reloads everything. Overwrites each view only if its own request succeeded. */
  async refresh(): Promise<void> {
    if (this._loading()) {
      return;
    }
    this._loading.set(true);
    const [overview, drift, queue] = await Promise.allSettled([
      this.client.overview(),
      this.client.drift(this.driftDays),
      this.client.queue(),
    ]);

    if (overview.status === 'fulfilled') {
      this._overview.set(overview.value);
    }
    if (drift.status === 'fulfilled') {
      this._drift.set(drift.value);
    }
    if (queue.status === 'fulfilled') {
      this._queue.set(queue.value);
    }
    const failure = [overview, drift, queue].find((result) => result.status === 'rejected');
    if (failure) {
      this._loadError.set(messageOf((failure as PromiseRejectedResult).reason, 'Could not load'));
    } else {
      this._loadError.set(null);
      this._lastUpdated.set(new Date());
    }
    this._loading.set(false);
  }

  async setDriftWindow(days: number): Promise<void> {
    this.driftDays = days;
    try {
      this._drift.set(await this.client.drift(days));
    } catch (error) {
      this._loadError.set(messageOf(error, 'Could not load drift reports'));
    }
  }

  /** Keeps refreshing while a retraining job is queued/running. Idempotent. */
  startPolling(intervalMs: number = POLL_INTERVAL_MS): void {
    if (this.pollHandle !== null) {
      return;
    }
    this.pollHandle = setInterval(() => {
      if (this.hasActiveJobs()) {
        void this.refresh();
      }
    }, intervalMs);
  }

  stopPolling(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /** Full detail (including the flagged samples) for one job; the caller holds the result. */
  loadJob(id: string): Promise<RetrainingJobDetail> {
    return this.client.job(id);
  }

  approveJob(id: string): Promise<boolean> {
    return this.act(`job:${id}`, () => this.client.approveJob(id));
  }

  submitJob(id: string): Promise<boolean> {
    return this.act(`job:${id}`, () => this.client.submitJob(id));
  }

  cancelJob(id: string): Promise<boolean> {
    return this.act(`job:${id}`, () => this.client.cancelJob(id));
  }

  resolveDrift(id: string): Promise<boolean> {
    return this.act(`drift:${id}`, () => this.client.resolveDrift(id));
  }

  promote(model: string, version: string): Promise<boolean> {
    return this.act(`model:${model}`, () => this.client.promote(model, version));
  }

  rollback(model: string): Promise<boolean> {
    return this.act(`model:${model}`, () => this.client.rollback(model));
  }

  /** Runs one action with its own busy flag, then reloads so the page shows the real outcome
   * (the server may have moved a job further than the response says). Returns whether it worked. */
  private async act(key: string, action: () => Promise<unknown>): Promise<boolean> {
    if (this.isBusy(key)) {
      return false;
    }
    this._actionError.set(null);
    this._busy.update((keys) => new Set(keys).add(key));
    let ok = true;
    try {
      await action();
    } catch (error) {
      ok = false;
      this._actionError.set(messageOf(error, 'Action failed'));
    } finally {
      this._busy.update((keys) => {
        const next = new Set(keys);
        next.delete(key);
        return next;
      });
    }
    await this.refreshAfterAction();
    return ok;
  }

  private async refreshAfterAction(): Promise<void> {
    // A poll may be mid-flight; wait it out so the reload reflects the action rather than being
    // skipped by refresh()'s "already loading" guard.
    while (this._loading()) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    await this.refresh();
  }
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
