import { Component, Signal, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OrchestratorInferenceResult, OrchestratorRunMode } from './models/orchestrator.models';
import { WorkService } from './work.service';

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

/** The Work tab: a web recreation of orchestrator-agent/adc_agentic_project's tkinter
 * ADCApplication (ui.py) - kept close to that app's own layout (Input Selection / Policy + LLM
 * Planner / button bar / Workflow Summary / Execution Log, in the same order) rather than
 * redesigned, so it reads as the same tool. The two unavoidable web adaptations: file pickers
 * replace free-text path Entries + a native Browse dialog (a browser can't resolve an arbitrary
 * typed path), and "Output JSON" is now just the filename used for the post-run
 * Download-result.json button, not a pre-chosen save path (no browser API for that). Never
 * reachable from the Chat tab and never calls /api/chat/stream - see
 * work.service.ts/work-orchestrator-client.ts. */
@Component({
  imports: [FormsModule],
  selector: 'app-work',
  styleUrl: './work.css',
  templateUrl: './work.html',
})
export class Work {
  protected readonly datasetFile = signal<File | null>(null);
  protected readonly xmlFile = signal<File | null>(null);
  protected readonly imageRootFiles = signal<File[]>([]);
  protected readonly outputFilename = signal('result.json');

  protected readonly featureThreshold = signal(0.7);
  protected readonly defectThreshold = signal(0.7);
  protected readonly useLlm = signal(false);
  protected readonly llmModel = signal('');
  protected readonly llmFallback = signal(true);

  protected readonly uploading = signal(false);
  protected readonly uploadError = signal<string | null>(null);

  // Reporting drift / flagging samples for retraining, once a run has finished.
  protected readonly selectedSampleIds = signal<ReadonlySet<string>>(new Set());
  protected readonly driftModelName = signal('');
  protected readonly driftDescription = signal('');
  protected readonly retrainingReason = signal('');
  protected readonly monitoringBusy = signal(false);
  protected readonly monitoringError = signal<string | null>(null);
  protected readonly monitoringMessage = signal<string | null>(null);

  protected readonly canRun: Signal<boolean> = computed(
    () => this.datasetFile() !== null && this.xmlFile() !== null && !this.uploading() && !this.workService.running(),
  );

  protected readonly hasRun: Signal<boolean> = computed(() => this.workService.log().length > 0);

  protected readonly imageRootSummary: Signal<string> = computed(() => {
    const files = this.imageRootFiles();
    if (files.length === 0) {
      return '';
    }
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    return `${files.length} file${files.length === 1 ? '' : 's'} · ${formatBytes(totalBytes)}`;
  });

  protected readonly resultJson: Signal<string | null> = computed(() => {
    const result = this.workService.result();
    return result === null ? null : JSON.stringify(result, null, 2);
  });

  protected readonly results: Signal<OrchestratorInferenceResult[]> = computed(
    () => this.workService.result()?.results ?? [],
  );

  /** Distinct real model names (routing.service_model, not the routing key) actually seen in this
   * run's results - what the "Report drift" model picker offers. A sample that never reached
   * stage 2 (e.g. FEATURE_CLASSIFICATION_UNCERTAIN) contributes nothing here. */
  protected readonly modelsInResults: Signal<string[]> = computed(() => {
    const names = new Set<string>();
    for (const sample of this.results()) {
      const name = sample.routing?.service_model;
      if (name) {
        names.add(name);
      }
    }
    return [...names].sort();
  });

  protected readonly selectedSamples: Signal<OrchestratorInferenceResult[]> = computed(() => {
    const ids = this.selectedSampleIds();
    return this.results().filter((sample) => ids.has(sample.sample_id));
  });

  protected readonly canReportDrift: Signal<boolean> = computed(
    () => this.driftModelName() !== '' && this.driftDescription().trim() !== '' && !this.monitoringBusy(),
  );

  protected readonly canFlagSelected: Signal<boolean> = computed(
    () => this.selectedSampleIds().size > 0 && this.retrainingReason().trim() !== '' && !this.monitoringBusy(),
  );

  // Mirrors ui.py's one-time `os.getenv("OPENAI_API_KEY")` check next to its "Use real LLM
  // Planner" checkbox - null while the initial GET /api/orchestrator/status is in flight (or if
  // it fails, e.g. orchestrator_agent_enabled is off), so the label only appears once it's known.
  protected readonly llmConfigured = signal<boolean | null>(null);

  constructor(protected readonly workService: WorkService) {
    this.workService
      .getStatus()
      .then((status) => this.llmConfigured.set(status.llm_configured))
      .catch(() => this.llmConfigured.set(null));
  }

  onDatasetFileChange(event: Event): void {
    this.datasetFile.set((event.target as HTMLInputElement).files?.[0] ?? null);
  }

  onXmlFileChange(event: Event): void {
    this.xmlFile.set((event.target as HTMLInputElement).files?.[0] ?? null);
  }

  onImageRootFilesChange(event: Event): void {
    const files = (event.target as HTMLInputElement).files;
    this.imageRootFiles.set(files ? Array.from(files) : []);
  }

  async prepare(): Promise<void> {
    await this.runMode('prepare');
  }

  async prepareAndVerify(): Promise<void> {
    await this.runMode('prepare_verify');
  }

  async runFull(): Promise<void> {
    await this.runMode('run_full');
  }

  clearLog(): void {
    this.workService.clearLog();
    this.selectedSampleIds.set(new Set());
    this.monitoringError.set(null);
    this.monitoringMessage.set(null);
  }

  confidencePercent(sample: OrchestratorInferenceResult): number | null {
    const confidence = sample.defect_classification?.confidence;
    return confidence === undefined ? null : Math.round(confidence * 100);
  }

  isSampleSelected(sampleId: string): boolean {
    return this.selectedSampleIds().has(sampleId);
  }

  toggleSample(sampleId: string): void {
    this.selectedSampleIds.update((ids) => {
      const next = new Set(ids);
      if (!next.delete(sampleId)) {
        next.add(sampleId);
      }
      return next;
    });
  }

  async reportDrift(): Promise<void> {
    if (!this.canReportDrift()) {
      return;
    }
    this.monitoringBusy.set(true);
    this.monitoringError.set(null);
    this.monitoringMessage.set(null);
    try {
      await this.workService.reportDrift({
        model_name: this.driftModelName(),
        description: this.driftDescription().trim(),
        samples: this.results(),
      });
      this.driftDescription.set('');
      this.monitoringMessage.set(`Drift report filed for ${this.driftModelName()}.`);
    } catch (error) {
      this.monitoringError.set(error instanceof Error ? error.message : 'Could not file the drift report.');
    } finally {
      this.monitoringBusy.set(false);
    }
  }

  async flagSelectedForRetraining(): Promise<void> {
    if (!this.canFlagSelected()) {
      return;
    }
    const reason = this.retrainingReason().trim();
    this.monitoringBusy.set(true);
    this.monitoringError.set(null);
    this.monitoringMessage.set(null);
    try {
      const tickets = await this.workService.flagForRetraining({
        tickets: this.selectedSamples().map((sample) => ({ sample, reason })),
      });
      this.monitoringMessage.set(
        `Flagged ${tickets.length} sample${tickets.length === 1 ? '' : 's'} for retraining.`,
      );
      this.selectedSampleIds.set(new Set());
      this.retrainingReason.set('');
    } catch (error) {
      this.monitoringError.set(
        error instanceof Error ? error.message : 'Could not flag the selected samples.',
      );
    } finally {
      this.monitoringBusy.set(false);
    }
  }

  downloadResult(): void {
    const json = this.resultJson();
    if (json === null) {
      return;
    }
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = this.outputFilename().trim() || 'result.json';
    link.click();
    // Revoking on the same tick races the browser actually starting the download - some
    // browsers haven't read the blob yet, so the save can silently fail or produce a truncated
    // file. Deferring a tick gives the download a chance to begin first.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private async runMode(mode: OrchestratorRunMode): Promise<void> {
    const dataset = this.datasetFile();
    const xml = this.xmlFile();
    if (!dataset || !xml) {
      return;
    }

    this.uploadError.set(null);
    this.uploading.set(true);
    try {
      const datasetId = await this.workService.uploadDataset(dataset);
      const xmlId = await this.workService.uploadXml(xml);
      const imageRootFiles = this.imageRootFiles();
      const imageRootId = imageRootFiles.length
        ? await this.workService.uploadImageRootFiles(imageRootFiles)
        : null;

      this.workService.run(mode, datasetId, xmlId, imageRootId, {
        featureThreshold: this.featureThreshold(),
        defectThreshold: this.defectThreshold(),
        useLlm: this.useLlm(),
        llmModel: this.llmModel(),
        llmFallback: this.llmFallback(),
      });
    } catch (error) {
      this.uploadError.set(error instanceof Error ? error.message : 'Upload failed.');
    } finally {
      this.uploading.set(false);
    }
  }
}
