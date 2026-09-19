import { Component, Signal, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OrchestratorRunMode } from '../models/orchestrator.models';
import { WorkService } from '../work.service';

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
 * ADCApplication (ui.py) - dataset/XML/image-root inputs, policy + LLM planner controls, the
 * three run buttons, a live Workflow Summary, and an Execution Log. Never reachable from the Chat
 * tab and never calls /api/chat/stream - see work.service.ts/work-orchestrator-client.ts. */
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

  protected readonly featureThreshold = signal(0.7);
  protected readonly defectThreshold = signal(0.7);
  protected readonly useLlm = signal(false);
  protected readonly llmModel = signal('');
  protected readonly llmFallback = signal(true);

  protected readonly showAdvanced = signal(false);

  protected readonly uploading = signal(false);
  protected readonly uploadError = signal<string | null>(null);

  protected readonly canRun: Signal<boolean> = computed(
    () => this.datasetFile() !== null && this.xmlFile() !== null && !this.uploading() && !this.workService.running(),
  );

  protected readonly hasRun: Signal<boolean> = computed(() => this.workService.log().length > 0);

  protected readonly imageRootSummary: Signal<string | null> = computed(() => {
    const files = this.imageRootFiles();
    if (files.length === 0) {
      return null;
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

  removeDatasetFile(input: HTMLInputElement): void {
    this.datasetFile.set(null);
    input.value = '';
  }

  removeXmlFile(input: HTMLInputElement): void {
    this.xmlFile.set(null);
    input.value = '';
  }

  removeImageRootFiles(input: HTMLInputElement): void {
    this.imageRootFiles.set([]);
    input.value = '';
  }

  toggleAdvanced(): void {
    this.showAdvanced.update((value) => !value);
  }

  statusBadgeClasses(status: string): string {
    switch (status) {
      case 'RUNNING':
        return 'bg-cyan-100 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300';
      case 'COMPLETED':
        return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300';
      case 'REVIEW_REQUIRED':
        return 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300';
      case 'ABORTED':
        return 'bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300';
      default:
        return 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300';
    }
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
    link.download = 'result.json';
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
