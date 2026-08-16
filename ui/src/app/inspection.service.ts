import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface Detection {
  label: string;
  confidence: number;
  bbox: [number, number, number, number];
}

export type Decision = 'auto_accept' | 'escalate_to_human' | 'rejected_quality';

export interface WorkflowResult {
  workflow_id: string;
  decision: Decision;
  detections: Detection[];
  overall_confidence: number;
  rationale: string;
}

/**
 * Talks to the FastAPI backend's POST /inspections - the same contract
 * simulation/simulate_line.py uses. Kept as an HTTP client, not a direct import of the
 * Python backend, so the UI stays a genuinely separate, swappable edge.
 */
@Injectable({ providedIn: 'root' })
export class InspectionService {
  private readonly apiUrl = 'http://localhost:8000';

  constructor(private readonly http: HttpClient) {}

  runInspection(file: File): Observable<WorkflowResult> {
    const formData = new FormData();
    formData.append('image', file, file.name);
    formData.append('metadata', '{}');
    return this.http.post<WorkflowResult>(`${this.apiUrl}/inspections`, formData);
  }
}
