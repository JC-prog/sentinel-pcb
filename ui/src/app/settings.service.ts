import { Injectable, signal } from '@angular/core';

export type LlmProvider = 'ollama' | 'openai';

const PROVIDER_STORAGE_KEY = 'sentinel-chat.llm-provider';
const OPENAI_API_KEY_STORAGE_KEY = 'sentinel-chat.openai-api-key';

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage unavailable (private browsing) - the choice just won't persist across reloads.
  }
}

function initialProvider(): LlmProvider {
  return readStorage(PROVIDER_STORAGE_KEY) === 'openai' ? 'openai' : 'ollama';
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  readonly provider = signal<LlmProvider>(initialProvider());
  /** Bring-your-own-key: kept only in this browser's localStorage, sent with each chat request,
   * never stored server-side - see HttpChatResponder. */
  readonly openaiApiKey = signal<string>(readStorage(OPENAI_API_KEY_STORAGE_KEY) ?? '');

  readonly isOpen = signal(false);

  open(): void {
    this.isOpen.set(true);
  }

  close(): void {
    this.isOpen.set(false);
  }

  setProvider(provider: LlmProvider): void {
    this.provider.set(provider);
    writeStorage(PROVIDER_STORAGE_KEY, provider);
  }

  setOpenaiApiKey(key: string): void {
    this.openaiApiKey.set(key);
    writeStorage(OPENAI_API_KEY_STORAGE_KEY, key);
  }
}
