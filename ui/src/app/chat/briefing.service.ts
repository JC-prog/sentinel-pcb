import { Injectable, inject } from '@angular/core';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';

/** What is waiting for an Admin in the Models tab (GET /api/chat/briefing). */
export interface Briefing {
  open_drift_reports: number;
  open_auto_reports: number;
  plans_awaiting_approval: number;
  corrections_waiting: number;
}

/** The Admin's opening line on chat. A convenience: any failure is "nothing to show", never an
 * error on the chat screen. */
@Injectable({ providedIn: 'root' })
export class BriefingService {
  private readonly auth = inject(AuthService);

  async load(): Promise<Briefing | null> {
    try {
      const response = await this.auth.fetchWithAuth(`${environment.apiBaseUrl}/api/chat/briefing`);
      return response.ok ? ((await response.json()) as Briefing) : null;
    } catch {
      return null;
    }
  }

  /** The items worth a line, most pressing first; empty when nothing is waiting. */
  static items(briefing: Briefing): string[] {
    const items: string[] = [];
    if (briefing.plans_awaiting_approval > 0) {
      const n = briefing.plans_awaiting_approval;
      items.push(`${n} retraining plan${n === 1 ? '' : 's'} awaiting your approval`);
    }
    if (briefing.open_drift_reports > 0) {
      const n = briefing.open_drift_reports;
      const auto = briefing.open_auto_reports;
      items.push(
        `${n} open drift report${n === 1 ? '' : 's'}` + (auto > 0 ? ` (${auto} raised automatically)` : ''),
      );
    }
    if (briefing.corrections_waiting > 0) {
      const n = briefing.corrections_waiting;
      items.push(`${n} correction${n === 1 ? '' : 's'} not yet in a plan`);
    }
    return items;
  }
}
