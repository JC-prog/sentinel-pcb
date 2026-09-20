import { InjectionToken } from '@angular/core';
import { Observable, from, of } from 'rxjs';
import { concatMap, delay } from 'rxjs/operators';

/**
 * One item of a streaming reply: either a text chunk to append to the assistant message, or a
 * transient "a tool is running" progress notice (e.g. "Calling Explainability Agent...") that's
 * never appended to message content - ChatService surfaces it as a separate, short-lived status
 * signal instead. Mirrors the backend's `event: delta` / `event: tool_call` SSE frames
 * (app/main.py's _chat_sse).
 */
export type ChatResponderEvent =
  | { type: 'delta'; text: string }
  | { type: 'toolCall'; label: string };

/**
 * Swap point for the real backend: provide CHAT_RESPONDER with an implementation that calls
 * your API (e.g. HttpChatResponder posting to FastAPI) instead of MockChatResponder.
 *
 * respond() emits zero or more events (in order) and then completes - a streaming reply, not a
 * single value. ChatService accumulates 'delta' events into one assistant message as they
 * arrive, and surfaces 'toolCall' events as a transient status instead.
 */
export interface ChatResponder {
  respond(
    conversationId: string,
    message: string,
    images: File[],
    xmlFiles?: File[],
  ): Observable<ChatResponderEvent>;
}

export const CHAT_RESPONDER = new InjectionToken<ChatResponder>('CHAT_RESPONDER');

export class MockChatResponder implements ChatResponder {
  respond(
    conversationId: string,
    message: string,
    images: File[],
    xmlFiles: File[] = [],
  ): Observable<ChatResponderEvent> {
    const imageNote = images.length
      ? ` I also see ${images.length} image${images.length === 1 ? '' : 's'} attached.`
      : '';
    const xmlNote = xmlFiles.length ? ` Plus an inspection XML attached.` : '';
    const reply = `This is a mocked response - no backend is wired up yet. You said: "${message}".${imageNote}${xmlNote}`;
    const words = reply.split(' ');
    return from(words).pipe(
      concatMap((word, i) =>
        of<ChatResponderEvent>({ type: 'delta', text: word + (i < words.length - 1 ? ' ' : '') }).pipe(
          delay(40),
        ),
      ),
    );
  }
}
