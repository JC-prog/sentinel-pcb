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
  | 'POLICY_DECISION'
  | 'ACCEPTED'
  | 'LEARNING_QUEUE'
  | 'EXPLANATION'
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
  /** The defect classifier's confidence - what actually gates auto-accept/escalate. */
  overall_confidence: number | null;
  feature_confidence: number | null;
  defect_label: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface Claim {
  text: string;
  supported: boolean;
  evidence_ref: string;
}

export interface Precedent {
  workflow_id: string;
  board_id: string | null;
  component_id: string | null;
  defect_label: string | null;
  decision: string | null;
}

export interface Guidance {
  title: string;
  defect_label: string | null;
  content: string;
}

export interface Explanation {
  claims: Claim[];
  stripped_claims: Claim[];
  grounded_flag: boolean;
  recommendation: string;
  retrieved_precedents: Precedent[];
  retrieved_guidance: Guidance[];
  prompt_version: string;
}

export interface WorkflowDetail extends WorkflowSummary {
  metadata: Record<string, string>;
  detections: Detection[] | null;
  rationale: string | null;
  explanation: Explanation | null;
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

  /** Omit both for everything. `terminal` picks Queue (false) vs. History (true). A specific
   * `status` (e.g. "LEARNING_QUEUE") takes precedence over `terminal`. */
  list(terminal?: boolean, status?: WorkflowState): Observable<WorkflowSummary[]> {
    let params = new HttpParams();
    if (status !== undefined) {
      params = params.set('status', status);
    } else if (terminal !== undefined) {
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
