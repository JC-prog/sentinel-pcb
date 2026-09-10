import { CommonModule } from '@angular/common';
import { Component, Input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { CaseContextResult, WorkflowsService } from '../workflows.service';

interface Message {
  question: string;
  result: CaseContextResult | null;
  error: string | null;
}

/** Case-scoped Q&A: re-runs Explainability & Review's Case Context retrieval on demand against a
 * reviewer's own question, instead of only the fixed snapshot shown elsewhere on the Report page.
 * Search-style (ranked precedents/guidance), not a synthesized LLM answer - see
 * app/agents/explainability_review/case_context.py. */
@Component({
  selector: 'app-case-chat',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './case-chat.html',
})
export class CaseChat {
  @Input({ required: true }) workflowId!: string;

  protected readonly messages = signal<Message[]>([]);
  protected readonly question = signal('');
  protected readonly loading = signal(false);

  constructor(private readonly workflowsService: WorkflowsService) {}

  ask(): void {
    const question = this.question().trim();
    if (!question || this.loading()) {
      return;
    }

    const index = this.messages().length;
    this.messages.update((messages) => [...messages, { question, result: null, error: null }]);
    this.question.set('');
    this.loading.set(true);

    const patch = (patch: Partial<Message>) => {
      this.messages.update((messages) =>
        messages.map((message, i) => (i === index ? { ...message, ...patch } : message)),
      );
      this.loading.set(false);
    };

    this.workflowsService.queryCaseContext(this.workflowId, question).subscribe({
      next: (result) => patch({ result }),
      error: () => patch({ error: "Couldn't reach Case Context - try again." }),
    });
  }
}
