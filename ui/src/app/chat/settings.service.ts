import { Injectable, signal } from '@angular/core';

/** Chat runs on OpenAI only, through the server's key. The field is still sent with every request
 * so the API contract (`provider`) is unchanged. */
export type LlmProvider = 'openai';

const MODEL_STORAGE_KEY = 'sentinel-chat.model';

function readModel(): string | null {
  try {
    return localStorage.getItem(MODEL_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeModel(model: string | null): void {
  try {
    if (model === null) {
      localStorage.removeItem(MODEL_STORAGE_KEY);
    } else {
      localStorage.setItem(MODEL_STORAGE_KEY, model);
    }
  } catch {
    // Storage unavailable (private browsing) - the choice just won't persist across reloads.
  }
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  readonly provider = signal<LlmProvider>('openai');

  /** The chat model the user picked; null means the server's default. */
  readonly model = signal<string | null>(readModel());
  /** What the server offers (GET /api/chat/models), empty until it has been asked. */
  readonly models = signal<string[]>([]);
  readonly defaultModel = signal<string | null>(null);

  readonly isOpen = signal(false);

  open(): void {
    this.isOpen.set(true);
  }

  close(): void {
    this.isOpen.set(false);
  }

  setModel(model: string | null): void {
    this.model.set(model);
    writeModel(model);
  }

  /** Records what the server offers, and drops a stored choice it no longer allows. */
  setAvailableModels(models: string[], defaultModel: string): void {
    this.models.set(models);
    this.defaultModel.set(defaultModel);
    const chosen = this.model();
    if (chosen !== null && !models.includes(chosen)) {
      this.setModel(null);
    }
  }
}
