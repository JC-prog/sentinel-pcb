import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Subscription, interval, startWith, switchMap } from 'rxjs';
import { WorkflowSummary, WorkflowsService } from '../workflows.service';

@Component({
  selector: 'app-queue',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './queue.html',
})
export class Queue implements OnInit, OnDestroy {
  protected readonly selectedFiles = signal<File[]>([]);
  protected readonly boardId = signal('');
  protected readonly componentId = signal('');
  protected readonly recipeId = signal('');
  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly activeWorkflows = signal<WorkflowSummary[]>([]);

  private pollSubscription?: Subscription;

  constructor(private readonly workflowsService: WorkflowsService) {}

  ngOnInit(): void {
    this.pollSubscription = interval(2000)
      .pipe(
        startWith(0),
        switchMap(() => this.workflowsService.list(false)),
      )
      .subscribe({
        next: (workflows) => this.activeWorkflows.set(workflows),
        error: () =>
          this.error.set(
            'Could not reach the API - is `uv run uvicorn app.main:app` running on :8000?',
          ),
      });
  }

  ngOnDestroy(): void {
    this.pollSubscription?.unsubscribe();
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFiles.set(Array.from(input.files ?? []));
  }

  onBoardIdInput(event: Event): void {
    this.boardId.set((event.target as HTMLInputElement).value);
  }

  onComponentIdInput(event: Event): void {
    this.componentId.set((event.target as HTMLInputElement).value);
  }

  onRecipeIdInput(event: Event): void {
    this.recipeId.set((event.target as HTMLInputElement).value);
  }

  submit(): void {
    const files = this.selectedFiles();
    if (!files.length) {
      return;
    }
    this.submitting.set(true);
    this.error.set(null);

    const boardInfo = {
      boardId: this.boardId() || undefined,
      componentId: this.componentId() || undefined,
      recipeId: this.recipeId() || undefined,
    };

    let remaining = files.length;
    for (const file of files) {
      this.workflowsService.submit(file, boardInfo).subscribe({
        next: () => {
          remaining -= 1;
          if (remaining === 0) {
            this.submitting.set(false);
            this.selectedFiles.set([]);
          }
        },
        error: (err) => {
          this.error.set(`Submit failed: ${err.status} ${err.statusText}`);
          this.submitting.set(false);
        },
      });
    }
  }
}
