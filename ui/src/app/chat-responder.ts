import { InjectionToken } from '@angular/core';
import { Observable, of } from 'rxjs';
import { delay } from 'rxjs/operators';

/**
 * Swap point for the real backend: provide CHAT_RESPONDER with an implementation that calls
 * your API (e.g. an HttpChatResponder posting to FastAPI) instead of MockChatResponder.
 */
export interface ChatResponder {
  respond(message: string, images: File[]): Observable<string>;
}

export const CHAT_RESPONDER = new InjectionToken<ChatResponder>('CHAT_RESPONDER');

export class MockChatResponder implements ChatResponder {
  respond(message: string, images: File[]): Observable<string> {
    const imageNote = images.length
      ? ` I also see ${images.length} image${images.length === 1 ? '' : 's'} attached.`
      : '';
    const reply = `This is a mocked response - no backend is wired up yet. You said: "${message}".${imageNote}`;
    return of(reply).pipe(delay(600));
  }
}
