import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface Detection {
  label: string;
  confidence: number;
  bbox: [number, number, number, number];
}

export type WorkflowState =
  | 'RECEIVED'
  | 'PREPARING'
  | 'QUALITY_CHECK'
  | 'INFERENCE'
  | 'COMPLETED'
  | 'HUMAN_REVIEW'
  | 'REJECTED_QUALITY';

export type Decision = 'auto_accept' | 'escalate_to_human' | 'rejected_quality';

export interface WorkflowSummary {
  workflow_id: string;
  status: WorkflowState;
  board_id: string | null;
  component_id: string | null;
  recipe_id: string | null;
  image_filename: string;
  decision: Decision | null;
  overall_confidence: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface WorkflowDetail extends WorkflowSummary {
  metadata: Record<string, string>;
  detections: Detection[] | null;
  rationale: string | null;
}

export interface BoardInfo {
  boardId?: string;
  componentId?: string;
  recipeId?: string;
}

/**
 * Talks to the FastAPI backend's /workflows resource - the same contract
 * simulation/simulate_line.py uses. Kept as an HTTP client, not a direct import of the Python
 * backend, so the UI stays a genuinely separate, swappable edge.
 */
@Injectable({ providedIn: 'root' })
export class WorkflowsService {
  readonly apiUrl = 'http://localhost:8000';

  constructor(private readonly http: HttpClient) {}

  submit(file: File, boardInfo: BoardInfo = {}): Observable<WorkflowSummary> {
    const formData = new FormData();
    formData.append('image', file, file.name);
    if (boardInfo.boardId) formData.append('board_id', boardInfo.boardId);
    if (boardInfo.componentId) formData.append('component_id', boardInfo.componentId);
    if (boardInfo.recipeId) formData.append('recipe_id', boardInfo.recipeId);
    formData.append('metadata', '{}');
    return this.http.post<WorkflowSummary>(`${this.apiUrl}/workflows`, formData);
  }

  /** Omit `terminal` for everything, `false` for the Queue view, `true` for History. */
  list(terminal?: boolean): Observable<WorkflowSummary[]> {
    let params = new HttpParams();
    if (terminal !== undefined) {
      params = params.set('terminal', String(terminal));
    }
    return this.http.get<WorkflowSummary[]>(`${this.apiUrl}/workflows`, { params });
  }

  get(workflowId: string): Observable<WorkflowDetail> {
    return this.http.get<WorkflowDetail>(`${this.apiUrl}/workflows/${workflowId}`);
  }

  imageUrl(workflowId: string): string {
    return `${this.apiUrl}/workflows/${workflowId}/image`;
  }

  traceUrl(workflowId: string): string {
    return `${this.apiUrl}/workflows/${workflowId}/trace`;
  }
}
