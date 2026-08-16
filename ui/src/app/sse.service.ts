import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface TraceEvent {
  id: number;
  actor: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  detail: string | null;
  created_at: string;
}

/**
 * Wraps the browser EventSource API in an Observable, for the Tracing view's live workflow-state
 * stream (GET /workflows/{id}/trace). Closing the EventSource on unsubscribe is what makes
 * Angular's automatic unsubscribe-on-destroy clean up the connection correctly.
 */
@Injectable({ providedIn: 'root' })
export class SseService {
  connect(url: string): Observable<TraceEvent> {
    return new Observable<TraceEvent>((subscriber) => {
      const source = new EventSource(url);

      source.addEventListener('transition', (event: MessageEvent) => {
        subscriber.next(JSON.parse(event.data));
      });
      source.addEventListener('done', () => {
        subscriber.complete();
      });
      source.onerror = () => {
        subscriber.error(new Error('SSE connection error'));
      };

      return () => source.close();
    });
  }
}
