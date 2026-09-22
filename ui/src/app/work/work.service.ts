import { Injectable, Signal, signal } from '@angular/core';
import {
  OrchestratorLogEntry,
  OrchestratorRunMode,
  OrchestratorRunResult,
  OrchestratorStatusEvent,
  WorkflowDriftReportRequest,
  WorkflowDriftReportOut,
  WorkflowRetrainingTicketOut,
  WorkflowRetrainingTicketsRequest,
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
};

@Injectable({ providedIn: 'root' })
export class WorkService {
  private readonly _status = signal<OrchestratorStatusEvent>(INITIAL_STATUS);
  private readonly _log = signal<OrchestratorLogEntry[]>([]);
  private readonly _running = signal(false);
  private readonly _result = signal<OrchestratorRunResult | null>(null);

  readonly status: Signal<OrchestratorStatusEvent> = this._status.asReadonly();
  readonly log: Signal<OrchestratorLogEntry[]> = this._log.asReadonly();
  readonly running: Signal<boolean> = this._running.asReadonly();
  readonly result: Signal<OrchestratorRunResult | null> = this._result.asReadonly();

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

  // Thin passthroughs, like the uploads above - the component (work.ts) owns the busy/error UI
  // state for these, the same way it already does around the upload calls.
  reportDrift(request: WorkflowDriftReportRequest): Promise<WorkflowDriftReportOut> {
    return this.client.reportDrift(request);
  }

  flagForRetraining(
    request: WorkflowRetrainingTicketsRequest,
  ): Promise<WorkflowRetrainingTicketOut[]> {
    return this.client.flagForRetraining(request);
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
    this._result.set(null);

    this.client
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
            this._log.update((entries) => [...entries, { kind: 'log', text: event.text }]);
          } else if (event.type === 'status') {
            this._status.set(event.status);
          } else if (event.type === 'planStep') {
            this._log.update((entries) => [...entries, { kind: 'plan_step', step: event.step }]);
          } else if (event.type === 'result') {
            this._result.set(event.result);
          } else if (event.type === 'error') {
            this._log.update((entries) => [...entries, { kind: 'error', message: event.message }]);
          }
        },
        error: (error: unknown) => {
          const message = error instanceof Error ? error.message : 'Run failed.';
          this._log.update((entries) => [...entries, { kind: 'error', message }]);
          this._running.set(false);
        },
        complete: () => this._running.set(false),
      });
  }
}
