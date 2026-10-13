import { Injectable, Signal, signal } from '@angular/core';
import { Subscription } from 'rxjs';
import {
  OrchestratorLogEntry,
  OrchestratorRunMode,
  OrchestratorRunResult,
  OrchestratorStatus,
  OrchestratorStatusEvent,
  WorkflowQueueCorrectionsOut,
  WorkflowQueueCorrectionsRequest,
  WorkflowRetrainingPlanOut,
  WorkflowReviewCaseOut,
  WorkflowRunDrift,
  WorkflowRunList,
  WorkflowRunSummary,
  WorkflowReviewDecisionOut,
  WorkflowReviewDecisionRequest,
} from './models/orchestrator.models';
import { WorkOrchestratorClient } from './work-orchestrator-client';

export interface WorkRunOptions {
  featureThreshold: number;
  defectThreshold: number;
  useLlm: boolean;
  llmModel: string;
  llmFallback: boolean;
}

const INITIAL_STATUS: OrchestratorStatusEvent = {
  status: 'Ready',
  input_samples: 0,
  preparation_ready: 0,
  verification_passed: 0,
  inference_attempted: 0,
  accepted: 0,
  review_required: 0,
  preparation_failed: 0,
  verification_failed: 0,
  inference_aborted: 0,
};

@Injectable({ providedIn: 'root' })
export class WorkService {
  private readonly _status = signal<OrchestratorStatusEvent>(INITIAL_STATUS);
  // Appended to in place and always notifying: a bulk run logs thousands of lines, and copying the
  // whole array for each one is wasted work. Consumers slice or filter it, never hold it.
  private readonly _log = signal<OrchestratorLogEntry[]>([], { equal: () => false });
  private readonly _running = signal(false);
  private readonly _result = signal<OrchestratorRunResult | null>(null);
  private readonly _reviewTick = signal(0);
  private readonly _runs = signal<WorkflowRunSummary[]>([]);
  private readonly _runsAvailable = signal(true);
  private readonly _runsError = signal<string | null>(null);
  private runSubscription: Subscription | null = null;

  readonly status: Signal<OrchestratorStatusEvent> = this._status.asReadonly();
  readonly log: Signal<OrchestratorLogEntry[]> = this._log.asReadonly();
  readonly running: Signal<boolean> = this._running.asReadonly();
  readonly result: Signal<OrchestratorRunResult | null> = this._result.asReadonly();
  /** The stored runs, newest first - the Work tab's run history (shown in the sidebar). */
  readonly runs: Signal<WorkflowRunSummary[]> = this._runs.asReadonly();
  /** False when the server could not read its run store, so an empty list is not "no runs". */
  readonly runsAvailable: Signal<boolean> = this._runsAvailable.asReadonly();
  /** Why the last open/refresh of a run failed, for the sidebar to show; null when fine. */
  readonly runsError: Signal<string | null> = this._runsError.asReadonly();
  /** Bumped each time Agent 2 finishes a sample during a run - the Review Console refetches its
   * cases on it. */
  readonly reviewTick: Signal<number> = this._reviewTick.asReadonly();

  constructor(private readonly client: WorkOrchestratorClient) {}

  uploadDataset(file: File): Promise<string> {
    return this.client.uploadDataset(file);
  }

  uploadXml(file: File): Promise<string> {
    return this.client.uploadXml(file);
  }

  uploadImageRootFiles(files: File[]): Promise<string> {
    return this.client.uploadImageRootFiles(files);
  }

  /** Refreshes the run history. Never throws: history is a convenience, not part of a run. */
  async loadRuns(): Promise<void> {
    try {
      const list: WorkflowRunList = await this.client.listRuns();
      this._runs.set(list.runs);
      this._runsAvailable.set(list.available);
      this._runsError.set(null);
    } catch (error) {
      this._runsError.set(error instanceof Error ? error.message : 'Could not load the runs.');
    }
  }

  /** Shows a stored run exactly as the run just finished would be: its counters, its result, and
   * (through the result's run id) the Review Console, Model feedback and Retraining queue. A run
   * in progress is not interrupted. */
  async openRun(runId: string): Promise<void> {
    if (this._running()) {
      return;
    }
    try {
      const run = await this.client.getRun(runId);
      this._log.set([]);
      this._status.set({
        status: String(run['workflow_status'] ?? ''),
        input_samples: Number(run['input_samples'] ?? 0),
        preparation_ready: Number(run['preparation_ready'] ?? 0),
        verification_passed: Number(run['verification_passed'] ?? 0),
        inference_attempted: Number(run['inference_attempted'] ?? 0),
        accepted: Number(run['accepted'] ?? 0),
        review_required: Number(run['review_required'] ?? 0),
        preparation_failed: Number(run['preparation_failed'] ?? 0),
        verification_failed: Number(run['verification_failed'] ?? 0),
        inference_aborted: Number(run['inference_aborted'] ?? 0),
      });
      this._result.set(run);
      this._runsError.set(null);
    } catch (error) {
      this._runsError.set(error instanceof Error ? error.message : 'Could not open that run.');
    }
  }

  getStatus(): Promise<OrchestratorStatus> {
    return this.client.getStatus();
  }

  // Thin passthroughs, like the uploads above - the component (work.ts) owns the busy/error UI
  // state for these, the same way it already does around the upload calls.
  getRunDrift(runId: string): Promise<WorkflowRunDrift> {
    return this.client.getRunDrift(runId);
  }

  queueCorrections(request: WorkflowQueueCorrectionsRequest): Promise<WorkflowQueueCorrectionsOut> {
    return this.client.queueCorrections(request);
  }

  draftRetrainingPlan(modelName: string): Promise<WorkflowRetrainingPlanOut> {
    return this.client.draftRetrainingPlan(modelName);
  }

  getReviewCases(runId: string): Promise<WorkflowReviewCaseOut[]> {
    return this.client.getReviewCases(runId);
  }

  getReviewImageUrl(
    runId: string,
    sampleId: string,
    kind: 'golden' | 'defect',
  ): Promise<string | null> {
    return this.client.getReviewImageUrl(runId, sampleId, kind);
  }

  saveReviewDecision(
    request: WorkflowReviewDecisionRequest,
  ): Promise<WorkflowReviewDecisionOut> {
    return this.client.saveReviewDecision(request);
  }

  private append(entry: OrchestratorLogEntry): void {
    this._log.update((entries) => {
      entries.push({ ...entry, at: Date.now() });
      return entries;
    });
  }

  clearLog(): void {
    this._log.set([]);
    this._result.set(null);
  }

  run(
    mode: OrchestratorRunMode,
    datasetId: string,
    xmlId: string,
    imageRootId: string | null,
    options: WorkRunOptions,
  ): void {
    if (this._running()) {
      return;
    }
    this._running.set(true);
    // Every run starts from a clean slate: the previous run's lines and counters would otherwise
    // sit in front of the new ones and read as one long run.
    this._log.set([]);
    this._status.set(INITIAL_STATUS);
    this._result.set(null);

    this.runSubscription = this.client
      .run({
        mode,
        dataset_id: datasetId,
        xml_id: xmlId,
        image_root_id: imageRootId,
        feature_threshold: options.featureThreshold,
        defect_threshold: options.defectThreshold,
        use_llm: options.useLlm,
        llm_model: options.llmModel || null,
        llm_fallback: options.llmFallback,
      })
      .subscribe({
        next: (event) => {
          if (event.type === 'log') {
            this.append({ kind: 'log', text: event.text });
          } else if (event.type === 'status') {
            this._status.set(event.status);
          } else if (event.type === 'planStep') {
            this.append({ kind: 'plan_step', step: event.step });
          } else if (event.type === 'result') {
            this._result.set(event.result);
          } else if (event.type === 'review') {
            this._reviewTick.update((tick) => tick + 1);
          } else if (event.type === 'error') {
            this.append({ kind: 'error', message: event.message });
          }
        },
        error: (error: unknown) => {
          const message = error instanceof Error ? error.message : 'Run failed.';
          this.append({ kind: 'error', message });
          this.finishRun();
        },
        complete: () => this.finishRun(),
      });
  }

  /** Stops the run in progress: unsubscribing aborts the streaming request, so the server's run
   * is told to stop and nothing more is appended. What was already logged stays. */
  cancel(): void {
    if (!this._running()) {
      return;
    }
    this.runSubscription?.unsubscribe();
    this.append({ kind: 'log', text: 'Run cancelled.' });
    this.finishRun();
  }

  private finishRun(): void {
    this.runSubscription = null;
    this._running.set(false);
    void this.loadRuns();
  }
}
