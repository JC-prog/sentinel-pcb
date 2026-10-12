import { Injectable, signal } from '@angular/core';

/** Chat runs on OpenAI only, through the server's key. The field is still sent with every request
 * so the API contract (`provider`) is unchanged. */
export type LlmProvider = 'openai';

@Injectable({ providedIn: 'root' })
export class SettingsService {
  readonly provider = signal<LlmProvider>('openai');

  readonly isOpen = signal(false);

  open(): void {
    this.isOpen.set(true);
  }

  close(): void {
    this.isOpen.set(false);
  }
}
