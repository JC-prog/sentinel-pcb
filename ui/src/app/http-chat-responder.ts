import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../environments/environment';
import { AuthService } from './auth.service';
import { ChatResponder } from './chat-responder';
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
      throw new Error(`Image upload failed: ${response.status}`);
    }
    const body: { id: string } = await response.json();
    return body.id;
  }

  respond(conversationId: string, message: string, images: File[]): Observable<string> {
    return new Observable<string>((subscriber) => {
      const controller = new AbortController();

      (async () => {
        const imageIds = await Promise.all(images.map((file) => this.uploadImage(file)));

        const provider = this.settings.provider();
        const response = await this.authService.fetchWithAuth(
          `${environment.apiBaseUrl}/api/chat/stream`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              conversation_id: conversationId,
              message,
              image_ids: imageIds,
              provider,
              openai_api_key: provider === 'openai' ? this.settings.openaiApiKey() : undefined,
            }),
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          throw new Error(`Chat stream failed: ${response.status}`);
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
              subscriber.next(String(frame.data['text']));
            } else if (frame.event === 'error') {
              throw new Error(String(frame.data['message'] ?? 'Chat stream error'));
            } else if (frame.event === 'done') {
              subscriber.complete();
              return;
            }
          }
        }
        subscriber.complete();
      })().catch((error: unknown) => subscriber.error(error));

      return () => controller.abort();
    });
  }
}
