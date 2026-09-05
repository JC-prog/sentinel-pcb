import { InjectionToken } from '@angular/core';
import { Observable, from, of } from 'rxjs';
import { concatMap, delay } from 'rxjs/operators';

/**
 * Swap point for the real backend: provide CHAT_RESPONDER with an implementation that calls
 * your API (e.g. HttpChatResponder posting to FastAPI) instead of MockChatResponder.
 *
 * respond() emits zero or more reply chunks (in order) and then completes - a streaming
 * reply, not a single value. ChatService accumulates the chunks into one assistant message as
 * they arrive.
 */
export interface ChatResponder {
  respond(conversationId: string, message: string, images: File[]): Observable<string>;
}

export const CHAT_RESPONDER = new InjectionToken<ChatResponder>('CHAT_RESPONDER');

export class MockChatResponder implements ChatResponder {
  respond(conversationId: string, message: string, images: File[]): Observable<string> {
    const imageNote = images.length
      ? ` I also see ${images.length} image${images.length === 1 ? '' : 's'} attached.`
      : '';
    const reply = `This is a mocked response - no backend is wired up yet. You said: "${message}".${imageNote}`;
    const words = reply.split(' ');
    return from(words).pipe(
      concatMap((word, i) => of(word + (i < words.length - 1 ? ' ' : '')).pipe(delay(40))),
    );
  }
}
