import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import { ChatResponder, ChatResponderError, ChatResponderEvent } from './chat-responder';
import { TOOL_RESULT_NAMES, ToolResult } from './models/chat.models';
import { SettingsService } from './settings.service';

interface SseFrame {
  event: string;
  data: Record<string, unknown>;
}

function parseSseFrame(raw: string): SseFrame {
  let event = 'message';
  let data = '{}';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      data = line.slice('data:'.length).trim();
    }
  }
  return { event, data: JSON.parse(data) };
}

/** What went wrong with a response, in words for the user. The server's own `detail` is used only
 * for statuses where it is written for people (a rejected file, a provider that isn't configured). */
async function describeFailure(response: Response, what: string): Promise<ChatResponderError> {
  let detail: string | null = null;
  try {
    const body: unknown = await response.json();
    const value = (body as { detail?: unknown } | null)?.detail;
    detail = typeof value === 'string' ? value : null;
  } catch {
    // not JSON - fall back to the status
  }
  const status = response.status;
  if (status === 401 || status === 403) {
    return new ChatResponderError('Your session has expired. Sign in again to continue.');
  }
  if (status === 413) {
    return new ChatResponderError(`${what}: that file is too large.`);
  }
  if (status === 422 && detail) {
    return new ChatResponderError(`${what}: ${detail}.`);
  }
  if (status === 429) {
    return new ChatResponderError('Too many requests. Wait a moment and try again.');
  }
  if (status === 503 && detail) {
    return new ChatResponderError(detail);
  }
  if (status >= 500) {
    return new ChatResponderError(`The server had a problem (HTTP ${status}). Try again.`);
  }
  return new ChatResponderError(`${what} (HTTP ${status}).`);
}

@Injectable()
export class HttpChatResponder implements ChatResponder {
  constructor(
    private readonly settings: SettingsService,
    private readonly authService: AuthService,
  ) {}

  private async uploadImage(file: File): Promise<string> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}/api/uploads`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      throw await describeFailure(response, `Couldn't upload ${file.name}`);
    }
    const body: { id: string } = await response.json();
    return body.id;
  }

  private async uploadXml(file: File): Promise<string> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await this.authService.fetchWithAuth(
      `${environment.apiBaseUrl}/api/uploads/xml`,
      { method: 'POST', body: formData },
    );
    if (!response.ok) {
      throw await describeFailure(response, `Couldn't upload ${file.name}`);
    }
    const body: { id: string } = await response.json();
    return body.id;
  }

  respond(
    conversationId: string,
    message: string,
    images: File[],
    xmlFiles: File[] = [],
  ): Observable<ChatResponderEvent> {
    return new Observable<ChatResponderEvent>((subscriber) => {
      const controller = new AbortController();

      (async () => {
        const imageIds = await Promise.all(images.map((file) => this.uploadImage(file)));
        const xmlIds = await Promise.all(xmlFiles.map((file) => this.uploadXml(file)));

        const provider = this.settings.provider();
        const model = this.settings.model();
        const response = await this.authService.fetchWithAuth(
          `${environment.apiBaseUrl}/api/chat/stream`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              conversation_id: conversationId,
              message,
              image_ids: imageIds,
              xml_ids: xmlIds,
              provider,
              ...(model !== null ? { model } : {}),
            }),
            signal: controller.signal,
          },
        );
        if (!response.ok) {
          throw await describeFailure(response, 'The assistant could not answer');
        }
        if (!response.body) {
          throw new ChatResponderError('The assistant sent no reply. Try again.');
        }

        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) {
            break;
          }
          buffer += value;

          let separatorIndex: number;
          while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
            const frame = parseSseFrame(buffer.slice(0, separatorIndex));
            buffer = buffer.slice(separatorIndex + 2);

            if (frame.event === 'delta') {
              subscriber.next({ type: 'delta', text: String(frame.data['text']) });
            } else if (frame.event === 'tool_call') {
              subscriber.next({ type: 'toolCall', label: String(frame.data['label']) });
            } else if (frame.event === 'tool_result') {
              const name = frame.data['name'] as ToolResult['name'];
              if (TOOL_RESULT_NAMES.includes(name)) {
                subscriber.next({
                  type: 'toolResult',
                  toolResult: { name, result: frame.data['result'] } as ToolResult,
                });
              }
            } else if (frame.event === 'error') {
              throw new ChatResponderError(
                String(frame.data['message'] ?? 'The assistant is unavailable right now.'),
              );
            } else if (frame.event === 'done') {
              const messageId = frame.data['message_id'];
              if (typeof messageId === 'string' && messageId) {
                subscriber.next({ type: 'messageId', id: messageId });
              }
              const usage = frame.data['usage'] as
                | { input_tokens?: unknown; output_tokens?: unknown }
                | undefined;
              if (usage && typeof usage === 'object') {
                subscriber.next({
                  type: 'usage',
                  inputTokens: Number(usage.input_tokens) || 0,
                  outputTokens: Number(usage.output_tokens) || 0,
                });
              }
              subscriber.complete();
              return;
            }
          }
        }
        subscriber.complete();
      })().catch((error: unknown) => {
        if (error instanceof ChatResponderError || controller.signal.aborted) {
          subscriber.error(error);
        } else {
          // fetch itself failing (offline, DNS, the connection dropping mid-stream)
          subscriber.error(
            new ChatResponderError("Can't reach the server. Check your connection and try again."),
          );
        }
      });

      return () => controller.abort();
    });
  }
}
