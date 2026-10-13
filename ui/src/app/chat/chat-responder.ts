import { InjectionToken } from '@angular/core';
import { Observable, from, of } from 'rxjs';
import { concatMap, delay } from 'rxjs/operators';
import { ToolResult } from './models/chat.models';

/**
 * One item of a streaming reply: either a text chunk to append to the assistant message, or a
 * transient "a tool is running" progress notice (e.g. "Calling Case lookup...") that's
 * never appended to message content - ChatService surfaces it as a separate, short-lived status
 * signal instead. A 'toolResult' carries a tool's structured result (an inspection, a relabel) to
 * be shown as a card on the assistant message. Mirrors the backend's `event: delta` /
 * `event: tool_call` / `event: tool_result` SSE frames (app/chat/services/streaming.py). A 'usage'
 * is the tokens the turn used, which the backend reports on the final `event: done` frame.
 */
export type ChatResponderEvent =
  | { type: 'delta'; text: string }
  | { type: 'toolCall'; label: string }
  | { type: 'toolResult'; toolResult: ToolResult }
  | { type: 'usage'; inputTokens: number; outputTokens: number };

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

/** A failure whose message is written for the user - the UI shows it as is. Anything else a
 * responder throws is unexpected and gets a generic message instead. */
export class ChatResponderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ChatResponderError';
  }
}

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
