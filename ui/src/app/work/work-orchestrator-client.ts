import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import {
  OrchestratorPlanStepEvent,
  OrchestratorRunRequest,
  OrchestratorStatusEvent,
  OrchestratorUploadRecord,
} from './models/orchestrator.models';

export type WorkResponderEvent =
  | { type: 'log'; text: string }
  | { type: 'status'; status: OrchestratorStatusEvent }
  | { type: 'planStep'; step: OrchestratorPlanStepEvent }
  | { type: 'result'; result: unknown }
  | { type: 'error'; message: string };

interface SseFrame {
  event: string;
  data: Record<string, unknown>;
}

/** Same tiny SSE-frame parser as http-chat-responder.ts, duplicated rather than shared - the Work
 * tab deliberately does not implement ChatResponder/use CHAT_RESPONDER, so chat and Work stay
 * decoupled end to end (the chat UI must never be able to reach the orchestrator agent, and vice
 * versa). */
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

@Injectable({ providedIn: 'root' })
export class WorkOrchestratorClient {
  constructor(private readonly authService: AuthService) {}

  async uploadDataset(file: File): Promise<string> {
    return this.upload('/api/orchestrator/uploads/dataset', file);
  }

  async uploadXml(file: File): Promise<string> {
    return this.upload('/api/orchestrator/uploads/xml', file);
  }

  /** `files` came from an <input webkitdirectory multiple> folder picker - each File's
   * webkitRelativePath is sent alongside it so the server can rebuild the folder structure
   * DatasetPreparationService's image-root remapping expects (see
   * app/agents/orchestrator_agent/uploads.py). */
  async uploadImageRootFiles(files: File[]): Promise<string> {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
      formData.append('relative_paths', file.webkitRelativePath || file.name);
    }
    const response = await this.authService.fetchWithAuth(
      `${environment.apiBaseUrl}/api/orchestrator/uploads/image-root`,
      { method: 'POST', body: formData },
    );
    if (!response.ok) {
      throw new Error(`Image root upload failed: ${response.status}`);
    }
    const body: OrchestratorUploadRecord = await response.json();
    return body.id;
  }

  private async upload(path: string, file: File): Promise<string> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}${path}`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`Upload failed: ${response.status}`);
    }
    const body: OrchestratorUploadRecord = await response.json();
    return body.id;
  }

  run(request: OrchestratorRunRequest): Observable<WorkResponderEvent> {
    return new Observable<WorkResponderEvent>((subscriber) => {
      const controller = new AbortController();

      (async () => {
        const response = await this.authService.fetchWithAuth(
          `${environment.apiBaseUrl}/api/orchestrator/run/stream`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request),
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          throw new Error(`Orchestrator run failed: ${response.status}`);
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

            if (frame.event === 'log') {
              subscriber.next({ type: 'log', text: String(frame.data['text']) });
            } else if (frame.event === 'status') {
              subscriber.next({ type: 'status', status: frame.data as unknown as OrchestratorStatusEvent });
            } else if (frame.event === 'plan_step') {
              subscriber.next({ type: 'planStep', step: frame.data as unknown as OrchestratorPlanStepEvent });
            } else if (frame.event === 'result') {
              subscriber.next({ type: 'result', result: frame.data });
            } else if (frame.event === 'error') {
              subscriber.next({ type: 'error', message: String(frame.data['message'] ?? 'Run error') });
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
