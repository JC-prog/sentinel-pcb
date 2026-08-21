import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, OnInit, signal } from '@angular/core';
import { WorkflowsService } from '../workflows.service';

interface Alert {
  metric: string;
  value: number;
  threshold: number;
  severity: 'warning' | 'critical';
  message: string;
}

interface ObservabilityReport {
  generated_at: string;
  total_workflows: number;
  decision_counts: Record<string, number>;
  escalation_rate: number;
  avg_feature_confidence: number | null;
  avg_defect_confidence: number | null;
  disagreement_rate: number;
  policy_violation_count: number;
  alerts: Alert[];
}

@Component({
  selector: 'app-observability',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './observability.html',
})
export class Observability implements OnInit {
  protected readonly report = signal<ObservabilityReport | null>(null);
  protected readonly error = signal<string | null>(null);

  constructor(
    private readonly http: HttpClient,
    private readonly workflowsService: WorkflowsService,
  ) {}

  ngOnInit(): void {
    this.http
      .get<ObservabilityReport>(`${this.workflowsService.apiUrl}/observability/metrics`)
      .subscribe({
        next: (report) => this.report.set(report),
        error: () =>
          this.error.set(
            'Could not reach the API - is `uv run uvicorn app.main:app` running on :8000?',
          ),
      });
  }
}
