import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'sentinel-work.settings';

export const DEFAULT_THRESHOLD = 0.7;

interface StoredSettings {
  featureThreshold: number;
  defectThreshold: number;
  useLlm: boolean;
  llmModel: string;
  llmFallback: boolean;
}

function readStorage(): Partial<StoredSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed !== null && typeof parsed === 'object' ? (parsed as Partial<StoredSettings>) : {};
  } catch {
    return {};
  }
}

function writeStorage(settings: StoredSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage unavailable (private browsing) - the choices just won't persist across reloads.
  }
}

/** A threshold is a confidence in 0..1; anything else (a cleared number input yields null) is not
 * accepted, so the run request never carries a value the backend would reject. */
function validThreshold(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : null;
}

/** The Work tab's per-run controls (policy thresholds and LLM planner options), kept apart from the
 * page so they live in a settings panel and survive a reload. These are exactly the fields the
 * run request already accepts - the planner's prompt itself is not editable. */
@Injectable({ providedIn: 'root' })
export class WorkSettingsService {
  readonly featureThreshold = signal(DEFAULT_THRESHOLD);
  readonly defectThreshold = signal(DEFAULT_THRESHOLD);
  readonly useLlm = signal(false);
  readonly llmModel = signal('');
  readonly llmFallback = signal(true);

  readonly isOpen = signal(false);

  constructor() {
    const stored = readStorage();
    this.featureThreshold.set(validThreshold(stored.featureThreshold) ?? DEFAULT_THRESHOLD);
    this.defectThreshold.set(validThreshold(stored.defectThreshold) ?? DEFAULT_THRESHOLD);
    this.useLlm.set(stored.useLlm === true);
    this.llmModel.set(typeof stored.llmModel === 'string' ? stored.llmModel : '');
    this.llmFallback.set(stored.llmFallback !== false);
  }

  open(): void {
    this.isOpen.set(true);
  }

  close(): void {
    this.isOpen.set(false);
  }

  setFeatureThreshold(value: unknown): void {
    this.update(() => {
      const next = validThreshold(value);
      if (next !== null) {
        this.featureThreshold.set(next);
      }
    });
  }

  setDefectThreshold(value: unknown): void {
    this.update(() => {
      const next = validThreshold(value);
      if (next !== null) {
        this.defectThreshold.set(next);
      }
    });
  }

  setUseLlm(value: boolean): void {
    this.update(() => this.useLlm.set(value));
  }

  setLlmModel(value: string): void {
    this.update(() => this.llmModel.set(value));
  }

  setLlmFallback(value: boolean): void {
    this.update(() => this.llmFallback.set(value));
  }

  private update(change: () => void): void {
    change();
    writeStorage({
      featureThreshold: this.featureThreshold(),
      defectThreshold: this.defectThreshold(),
      useLlm: this.useLlm(),
      llmModel: this.llmModel(),
      llmFallback: this.llmFallback(),
    });
  }
}
