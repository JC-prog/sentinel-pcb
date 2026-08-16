import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { WorkflowSummary, WorkflowsService } from '../workflows.service';

@Component({
  selector: 'app-history',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './history.html',
})
export class History implements OnInit {
  protected readonly workflows = signal<WorkflowSummary[]>([]);
  protected readonly error = signal<string | null>(null);

  constructor(private readonly workflowsService: WorkflowsService) {}

  ngOnInit(): void {
    this.workflowsService.list(true).subscribe({
      next: (workflows) => this.workflows.set(workflows),
      error: () =>
        this.error.set(
          'Could not reach the API - is `uv run uvicorn app.main:app` running on :8000?',
        ),
    });
  }
}
