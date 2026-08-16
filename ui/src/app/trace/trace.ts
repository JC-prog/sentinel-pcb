import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { SseService, TraceEvent } from '../sse.service';
import { WorkflowSummary, WorkflowsService } from '../workflows.service';

@Component({
  selector: 'app-trace',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './trace.html',
})
export class Trace implements OnInit, OnDestroy {
  protected readonly workflow = signal<WorkflowSummary | null>(null);
  protected readonly events = signal<TraceEvent[]>([]);
  protected readonly currentStatus = signal<string | null>(null);
  protected readonly done = signal(false);

  private subscription?: Subscription;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly workflowsService: WorkflowsService,
    private readonly sseService: SseService,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (!id) {
      return;
    }

    this.workflowsService.get(id).subscribe((workflow) => {
      this.workflow.set(workflow);
      this.currentStatus.set(workflow.status);
    });

    this.subscription = this.sseService.connect(this.workflowsService.traceUrl(id)).subscribe({
      next: (event) => {
        this.events.update((existing) => [...existing, event]);
        if (event.to_status) {
          this.currentStatus.set(event.to_status);
        }
      },
      complete: () => this.done.set(true),
      error: () => this.done.set(true),
    });
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
  }
}
