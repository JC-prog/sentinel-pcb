import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { BboxOverlay } from '../bbox-overlay/bbox-overlay';
import { WorkflowDetail, WorkflowsService } from '../workflows.service';

const NON_TERMINAL: readonly string[] = ['RECEIVED', 'PREPARING', 'QUALITY_CHECK', 'INFERENCE'];

@Component({
  selector: 'app-board-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, BboxOverlay],
  templateUrl: './board-detail.html',
})
export class BoardDetail implements OnInit {
  protected readonly workflow = signal<WorkflowDetail | null>(null);
  protected readonly error = signal<string | null>(null);
  protected imageUrl: string | null = null;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly workflowsService: WorkflowsService,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (!id) {
      return;
    }
    this.imageUrl = this.workflowsService.imageUrl(id);
    this.workflowsService.get(id).subscribe({
      next: (workflow) => this.workflow.set(workflow),
      error: () => this.error.set('Workflow not found.'),
    });
  }

  protected isInFlight(status: string): boolean {
    return NON_TERMINAL.includes(status);
  }
}
