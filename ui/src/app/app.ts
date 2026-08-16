import { CommonModule } from '@angular/common';
import { Component, signal } from '@angular/core';
import { BboxOverlay } from './bbox-overlay/bbox-overlay';
import { Decision, InspectionService, WorkflowResult } from './inspection.service';

interface HistoryEntry extends WorkflowResult {
  filename: string;
}

interface DecisionStyle {
  label: string;
  classes: string;
}

const DECISION_STYLES: Record<Decision, DecisionStyle> = {
  auto_accept: {
    label: 'AUTO ACCEPT',
    classes: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
  },
  escalate_to_human: {
    label: 'ESCALATE TO HUMAN',
    classes: 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  },
  rejected_quality: {
    label: 'REJECTED - QUALITY',
    classes: 'bg-rose-500/20 text-rose-400 border-rose-500/40',
  },
};

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, BboxOverlay],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly selectedFile = signal<File | null>(null);
  protected readonly imagePreviewUrl = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly history = signal<HistoryEntry[]>([]);
  protected readonly decisionStyles = DECISION_STYLES;

  constructor(private readonly inspectionService: InspectionService) {}

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.selectedFile.set(file);
    this.error.set(null);
    this.imagePreviewUrl.set(file ? URL.createObjectURL(file) : null);
  }

  runInspection(): void {
    const file = this.selectedFile();
    if (!file) {
      return;
    }
    this.loading.set(true);
    this.error.set(null);
    this.inspectionService.runInspection(file).subscribe({
      next: (result) => {
        this.history.update((entries) => [{ ...result, filename: file.name }, ...entries]);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(
          err.status === 0
            ? 'Could not reach the API - is `uv run uvicorn app.main:app` running on :8000?'
            : `Request failed: ${err.status} ${err.statusText}`,
        );
        this.loading.set(false);
      },
    });
  }

  protected get latest(): HistoryEntry | null {
    return this.history()[0] ?? null;
  }
}
