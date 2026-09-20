import { Component, Signal, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OrchestratorRunMode } from './models/orchestrator.models';
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

  constructor(protected readonly workService: WorkService) {}

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
