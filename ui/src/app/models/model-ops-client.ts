import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import {
  DriftOverview,
  DriftReport,
  ModelVersion,
  ModelsOverview,
  QueueOverview,
  RetrainingJobDetail,
} from './model-ops.models';

const BASE = '/api/models';

/** The API answers errors with `{detail: string}` (our own HTTPException) or, for request
 * validation, `{detail: [{msg, ...}]}` - same two shapes auth.service.ts handles. */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body: { detail?: unknown } = await response.json();
    const detail = body.detail;
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join('; ');
    }
  } catch {
    // not JSON - fall through to the generic message
  }
  return `Request failed (${response.status})`;
}

/** Thin HTTP layer for the Models tab (app/modelops/api/models.py). Like the Work tab's client it
 * is deliberately separate from the chat code: the two never share a client. Every call goes
 * through AuthService.fetchWithAuth so an expired access token is renewed silently. */
@Injectable({ providedIn: 'root' })
export class ModelOpsClient {
  constructor(private readonly authService: AuthService) {}

  overview(): Promise<ModelsOverview> {
    return this.request('GET', BASE);
  }

  drift(days: number): Promise<DriftOverview> {
    return this.request('GET', `${BASE}/drift?days=${days}`);
  }

  queue(): Promise<QueueOverview> {
    return this.request('GET', `${BASE}/retraining/queue`);
  }

  job(id: string): Promise<RetrainingJobDetail> {
    return this.request('GET', `${BASE}/retraining/jobs/${encodeURIComponent(id)}`);
  }

  approveJob(id: string): Promise<RetrainingJobDetail> {
    return this.request('POST', `${BASE}/retraining/jobs/${encodeURIComponent(id)}/approve`);
  }

  submitJob(id: string): Promise<RetrainingJobDetail> {
    return this.request('POST', `${BASE}/retraining/jobs/${encodeURIComponent(id)}/submit`);
  }

  cancelJob(id: string): Promise<RetrainingJobDetail> {
    return this.request('POST', `${BASE}/retraining/jobs/${encodeURIComponent(id)}/cancel`);
  }

  resolveDrift(id: string): Promise<DriftReport> {
    return this.request('POST', `${BASE}/drift/${encodeURIComponent(id)}/resolve`);
  }

  promote(model: string, version: string): Promise<ModelVersion> {
    return this.request('POST', `${BASE}/${encodeURIComponent(model)}/promote`, { version });
  }

  rollback(model: string): Promise<ModelVersion> {
    return this.request('POST', `${BASE}/${encodeURIComponent(model)}/rollback`);
  }

  private async request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const init: RequestInit = { method };
    if (body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}${path}`, init);
    if (!response.ok) {
      throw new Error(await errorMessage(response));
    }
    return (await response.json()) as T;
  }
}
