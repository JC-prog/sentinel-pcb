import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import { CaseDetail, CaseList, CaseQuery } from './cases.models';

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: { detail?: unknown } = await response.json();
    if (typeof body.detail === 'string' && body.detail) {
      return body.detail;
    }
  } catch {
    // not JSON - fall through to the generic message
  }
  return `Request failed (${response.status})`;
}

/** Thin HTTP layer for the Cases tab (app/chat/api/cases.py): read-only. Every call goes through
 * AuthService.fetchWithAuth so an expired access token is renewed silently. */
@Injectable({ providedIn: 'root' })
export class CasesClient {
  constructor(private readonly authService: AuthService) {}

  list(query: CaseQuery): Promise<CaseList> {
    const params = new URLSearchParams({
      limit: String(query.limit),
      offset: String(query.offset),
    });
    if (query.q.trim()) {
      params.set('q', query.q.trim());
    }
    if (query.status) {
      params.set('status', query.status);
    }
    if (query.model) {
      params.set('model', query.model);
    }
    if (query.corrected) {
      params.set('corrected', query.corrected === 'yes' ? 'true' : 'false');
    }
    return this.get(`/api/cases?${params.toString()}`);
  }

  detail(caseNumber: string): Promise<CaseDetail> {
    return this.get(`/api/cases/${encodeURIComponent(caseNumber)}`);
  }

  /** An object URL for the inspected image (an <img src> cannot carry the auth header). The caller
   * revokes it. Null when the file is gone. */
  async imageUrl(imageId: string): Promise<string | null> {
    const response = await this.authService.fetchWithAuth(
      `${environment.apiBaseUrl}/api/uploads/${encodeURIComponent(imageId)}`,
    );
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(await errorMessage(response));
    }
    return URL.createObjectURL(await response.blob());
  }

  private async get<T>(path: string): Promise<T> {
    const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}${path}`);
    if (!response.ok) {
      throw new Error(await errorMessage(response));
    }
    return (await response.json()) as T;
  }
}
