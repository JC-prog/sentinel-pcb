import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { CaseChat } from '../case-chat/case-chat';
import { WorkflowDetail, WorkflowsService } from '../workflows.service';

@Component({
  selector: 'app-report',
  standalone: true,
  imports: [CommonModule, RouterLink, CaseChat],
  templateUrl: './report.html',
})
export class Report implements OnInit {
  protected readonly workflow = signal<WorkflowDetail | null>(null);
  protected readonly error = signal<string | null>(null);

  constructor(
    private readonly route: ActivatedRoute,
    private readonly workflowsService: WorkflowsService,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (!id) {
      return;
    }
    this.workflowsService.get(id).subscribe({
      next: (workflow) => this.workflow.set(workflow),
      error: () => this.error.set('Workflow not found.'),
    });
  }
}
