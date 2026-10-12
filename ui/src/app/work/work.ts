import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  Signal,
  afterRenderEffect,
  computed,
  effect,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  OrchestratorRunMode,
  WorkflowDecisionSource,
  WorkflowCorrection,
  WorkflowReviewCaseOut,
  WorkflowRunDrift,
} from './models/orchestrator.models';
import { formatBytes, labelText, modelSaid, percent, sourceLabel, trainingStatus } from './work-format';
import { WorkSettingsService } from './work-settings.service';
import { WorkSettings } from './work-settings/work-settings';
import { WorkAlert } from './work-alert/work-alert';
import { WorkExecutionLog } from './work-execution-log/work-execution-log';
import { WorkService } from './work.service';
import { OrchestratorStatusEvent } from './models/orchestrator.models';

/** Samples that dropped out before a decision: failed preparation, failed verification, or aborted
 * in inference. Zero for an older server that doesn't send the three counts. */
export function failedCount(status: OrchestratorStatusEvent): number {
  return (
    (status.preparation_failed ?? 0) + (status.verification_failed ?? 0) + (status.inference_aborted ?? 0)
  );
}

/** IPC defect classes an operator can override with - the same canonical names Agent 2 and the
 * conflict check (app/workflow/services/reviews.py's normalize_defect) use. */
export const IPC_CLASSES = [
  'missing part',
  'wrong part',
  'shifted',
  'tombstone',
  'solder insufficient',
  'foreign material',
  'no defect',
] as const;

/** The Work tab: a web recreation of orchestrator-agent/adc_agentic_project's tkinter
 * ADCApplication (ui.py) - kept close to that app's own layout (Input Selection / Policy + LLM
 * Planner / button bar / Workflow Summary / Execution Log, in the same order) rather than
 * redesigned, so it reads as the same tool. The two unavoidable web adaptations: file pickers
 * replace free-text path Entries + a native Browse dialog (a browser can't resolve an arbitrary
 * typed path), and "Output JSON" is now just the filename used for the post-run
 * Download-result.json button, not a pre-chosen save path (no browser API for that). Never
 * reachable from the Chat tab and never calls /api/chat/stream - see
 * work.service.ts/work-orchestrator-client.ts. */
@Component({
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, RouterLink, WorkAlert, WorkExecutionLog, WorkSettings],
  selector: 'app-work',
  styleUrl: './work.css',
  templateUrl: './work.html',
})
export class Work implements OnDestroy {
  protected readonly datasetFile = signal<File | null>(null);
  protected readonly xmlFile = signal<File | null>(null);
  protected readonly imageRootFiles = signal<File[]>([]);

  protected readonly uploading = signal(false);
  protected readonly uploadError = signal<string | null>(null);

  // Sending corrections for retraining, from the Corrections & Retraining tab. One status line for
  // both steps (tickets, then the plan), cleared at the start of each action.
  protected readonly monitoringBusy = signal(false);
  protected readonly actionError = signal<string | null>(null);
  protected readonly actionMessage = signal<string | null>(null);

  // The Corrections & Retraining tab's view of the run: how often the defect model was wrong, judged
  // by the operator's final decisions (their relabels), computed on the server and refetched
  // whenever one is saved.
  protected readonly runDrift = signal<WorkflowRunDrift | null>(null);
  protected readonly driftLoading = signal(false);
  protected readonly driftError = signal<string | null>(null);
  /** Corrections the operator unticked. Everything sendable is ticked by default, so only the
   * exclusions are remembered. */
  protected readonly excludedCorrectionIds = signal<ReadonlySet<string>>(new Set());

  protected readonly corrections: Signal<WorkflowCorrection[]> = computed(
    () => this.runDrift()?.corrections ?? [],
  );
  /** Corrections that can still be sent: they have a model to train and no ticket yet. */
  protected readonly pendingCorrections: Signal<WorkflowCorrection[]> = computed(() =>
    this.corrections().filter((c) => c.queueable && !c.queued),
  );
  protected readonly selectedCorrections: Signal<WorkflowCorrection[]> = computed(() =>
    this.pendingCorrections().filter((c) => !this.excludedCorrectionIds().has(c.sample_id)),
  );
  protected readonly allCorrectionsSelected: Signal<boolean> = computed(
    () => this.pendingCorrections().length > 0 && this.selectedCorrections().length === this.pendingCorrections().length,
  );
  /** Models with samples from this run waiting for a retraining plan, with how many - normally
   * empty, since sending corrections also creates the plan; left over if that step failed. `others`
   * is how many more (from chat or other runs) the plan would also take. */
  protected readonly planModels: Signal<{ name: string; tickets: number; others: number }[]> = computed(
    () => {
      const drift = this.runDrift();
      const all = drift?.open_tickets ?? {};
      return Object.entries(drift?.run_open_tickets ?? {})
        .map(([name, tickets]) => ({ name, tickets, others: Math.max((all[name] ?? 0) - tickets, 0) }))
        .sort((a, b) => a.name.localeCompare(b.name));
    },
  );
  /** The flagged samples split into one bar: model right / wrong / unsure / not reviewed. */
  protected readonly outcomeSegments: Signal<
    { key: string; label: string; count: number; share: number; barClass: string }[]
  > = computed(() => {
    const drift = this.runDrift();
    if (!drift?.available) {
      return [];
    }
    const parts = [
      { key: 'confirmed', label: 'Model right', count: drift.totals.confirmed, barClass: 'bg-emerald-500' },
      { key: 'relabelled', label: 'Model wrong', count: drift.totals.relabelled, barClass: 'bg-red-500' },
      { key: 'no-answer', label: 'Model unsure', count: drift.totals.no_prediction, barClass: 'bg-amber-400' },
      { key: 'pending', label: 'Not reviewed yet', count: drift.decisions.pending, barClass: 'bg-slate-300 dark:bg-slate-600' },
    ];
    const total = parts.reduce((sum, part) => sum + part.count, 0);
    return parts.map((part) => ({ ...part, share: total === 0 ? 0 : (part.count / total) * 100 }));
  });
  /** The one-sentence answer to "where do my corrections stand?". */
  protected readonly feedbackStatus: Signal<{ kind: 'review' | 'clear' | 'send' | 'sent'; text: string }> = computed(() => {
    const drift = this.runDrift();
    const flagged = drift?.totals.review_required ?? 0;
    const pending = drift?.decisions.pending ?? 0;
    const corrections = this.corrections().length;
    const toSend = this.pendingCorrections().length;
    const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;
    if (pending > 0) {
      return { kind: 'review', text: `${plural(pending, 'flagged sample')} still need${pending === 1 ? 's' : ''} review before the feedback is complete.` };
    }
    if (corrections === 0) {
      return {
        kind: 'clear',
        text: flagged === 0 ? 'Nothing was flagged for review in this run.' : 'The model got every reviewed sample right. Nothing to send.',
      };
    }
    if (toSend === 0) {
      return { kind: 'sent', text: `All ${plural(corrections, 'correction')} have been sent.` };
    }
    return {
      kind: 'send',
      text: `You corrected ${corrections} of ${drift?.totals.decided ?? 0} reviewed samples. ${toSend} ${toSend === 1 ? 'is' : 'are'} ready to send.`,
    };
  });

  /** Where the corrections are on their way to a better model, stage by stage. */
  protected readonly feedbackStages: Signal<{ key: string; label: string; hint: string; count: number }[]> = computed(() => {
    const all = this.corrections();
    const count = (status: string): number => all.filter((c) => c.queued && c.ticket_status === status).length;
    return [
      { key: 'to-send', label: 'To send', hint: 'Waiting for you', count: this.pendingCorrections().length },
      { key: 'sent', label: 'Sent', hint: 'Waiting for a plan', count: count('open') },
      { key: 'approval', label: 'Awaiting Admin', hint: 'Approve in Models tab', count: count('acknowledged') },
      { key: 'retrained', label: 'Retrained', hint: 'Model updated', count: count('resolved') },
    ];
  });

  /** Headline numbers for the analytics section. */
  protected readonly feedbackKpis: Signal<{ label: string; value: string; hint: string }[]> = computed(() => {
    const drift = this.runDrift();
    if (!drift?.available) {
      return [];
    }
    const t = drift.totals;
    const answered = t.confirmed + t.relabelled;
    return [
      {
        label: 'Reviewed',
        value: t.review_required === 0 ? '—' : `${Math.round((t.decided / t.review_required) * 100)}%`,
        hint: `${t.decided} of ${t.review_required} flagged`,
      },
      {
        label: 'Model accuracy',
        value: answered === 0 ? '—' : `${Math.round((t.confirmed / answered) * 100)}%`,
        hint: answered === 0 ? 'nothing classified yet' : `${t.confirmed} of ${answered} right`,
      },
      { label: 'Corrections', value: String(t.corrected), hint: 'wrong or unsure' },
      { label: 'Model unsure', value: String(t.no_prediction), hint: 'no defect model ran' },
    ];
  });

  /** The outcome split as donut arcs (circumference 100, so a share is its own dash length). */
  protected readonly outcomeDonut: Signal<{ key: string; label: string; count: number; dash: string; offset: number; stroke: string }[]> = computed(() => {
    const strokes: Record<string, string> = {
      confirmed: '#10b981',
      relabelled: '#ef4444',
      'no-answer': '#fbbf24',
      pending: '#94a3b8',
    };
    let offset = 25; // start at 12 o'clock
    return this.outcomeSegments().map((s) => {
      const arc = { key: s.key, label: s.label, count: s.count, dash: `${s.share} ${100 - s.share}`, offset, stroke: strokes[s.key] };
      offset -= s.share;
      return arc;
    });
  });

  /** One bar per defect model: how often it was wrong on the reviewed samples. */
  protected readonly modelBars: Signal<{ name: string; rate: number; detail: string }[]> = computed(() =>
    (this.runDrift()?.models ?? [])
      .filter((m) => m.model_name !== null && m.correction_rate !== null)
      .map((m) => ({
        name: m.model_name as string,
        rate: (m.correction_rate as number) * 100,
        detail: `${m.corrected} of ${m.decided} reviewed`,
      })),
  );

  /** Who made the final call, and how often that call overruled the model. */
  protected readonly decisionSources: Signal<{ label: string; chosen: number; overruled: number; share: number }[]> = computed(() => {
    const by = this.runDrift()?.decisions.by_source ?? {};
    const rows = Object.entries(by).map(([source, v]) => ({
      label: sourceLabel(source),
      chosen: v.chosen,
      overruled: v.overruled_model,
    }));
    const max = Math.max(1, ...rows.map((r) => r.chosen));
    return rows.filter((r) => r.chosen > 0).map((r) => ({ ...r, share: (r.chosen / max) * 100 }));
  });

  /** The most common mistakes with a bar scaled to the commonest one. */
  protected readonly mistakeBars: Signal<{ from: string; to: string; count: number; share: number }[]> = computed(() => {
    const list = this.runDrift()?.decisions.top_overruled ?? [];
    const max = Math.max(1, ...list.map((m) => m.count));
    return list.map((m) => ({ from: m.model_label, to: m.chosen_label, count: m.count, share: (m.count / max) * 100 }));
  });

  protected readonly planBusy = signal(false);

  protected readonly canQueueCorrections: Signal<boolean> = computed(
    () => this.selectedCorrections().length > 0 && !this.monitoringBusy(),
  );

  // Agent 2 review + human-in-the-loop conflict resolution, once a run has finished.
  protected readonly ipcClasses = IPC_CLASSES;
  protected readonly reviewCases = signal<WorkflowReviewCaseOut[]>([]);
  protected readonly consoleOpen = signal(false);
  protected readonly consoleTab = signal<'review' | 'drift'>('review');
  protected readonly caseFilter = signal<'all' | 'pending' | 'reviewed'>('all');
  protected readonly selectedCaseId = signal<string | null>(null);
  protected readonly goldenImageUrl = signal<string | null>(null);
  protected readonly defectImageUrl = signal<string | null>(null);
  protected readonly decisionSource = signal<WorkflowDecisionSource>('MACHINE');
  protected readonly manualClass = signal<string>(IPC_CLASSES[0]);
  protected readonly operatorNotes = signal('');
  protected readonly reviewBusy = signal(false);
  protected readonly casesLoading = signal(false);
  protected readonly imagesLoading = signal(false);
  protected readonly reviewError = signal<string | null>(null);
  protected readonly reviewMessage = signal<string | null>(null);

  protected readonly canRun: Signal<boolean> = computed(
    () => this.datasetFile() !== null && this.xmlFile() !== null && !this.uploading() && !this.workService.running(),
  );

  protected readonly summaryTiles: Signal<{ label: string; value: number; tone: string }[]> = computed(() => {
    const status = this.workService.status();
    const failed = failedCount(status);
    const plain = 'border-slate-300 dark:border-slate-700';
    // Colour only where it means something: green for accepted, amber for what needs a person, red
    // for samples that dropped out before a decision.
    return [
      { label: 'Input', value: status.input_samples, tone: plain },
      { label: 'Prepared', value: status.preparation_ready, tone: plain },
      { label: 'Verified', value: status.verification_passed, tone: plain },
      { label: 'Inference', value: status.inference_attempted, tone: plain },
      {
        label: 'Failed',
        value: failed,
        tone: failed > 0 ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/30' : plain,
      },
      {
        label: 'Accepted',
        value: status.accepted,
        tone: status.accepted > 0 ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30' : plain,
      },
      {
        label: 'Review',
        value: status.review_required,
        tone: status.review_required > 0 ? 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30' : plain,
      },
    ];
  });

  /** The server's status word (REVIEW_REQUIRED) as a sentence-case label, with a light and what it
   * means for the samples. */
  protected readonly statusView: Signal<{ label: string; light: string; detail: string }> = computed(() => {
    const status = this.workService.status();
    const raw = status.status;
    const words = raw.replace(/_/g, ' ').toLowerCase();
    const label = words.charAt(0).toUpperCase() + words.slice(1);
    const upper = raw.toUpperCase();
    let light = 'bg-slate-400';
    if (upper.includes('REVIEW')) {
      light = 'bg-amber-500';
    } else if (/(FAIL|ABORT|ERROR)/.test(upper)) {
      light = 'bg-red-500';
    } else if (/(ACCEPT|COMPLETE|PASS|SUCCESS)/.test(upper)) {
      light = 'bg-emerald-500';
    }
    const parts: string[] = [];
    if (status.review_required > 0) {
      parts.push(
        `${status.review_required} of ${status.input_samples} sample${status.input_samples === 1 ? '' : 's'} need a person to confirm`,
      );
    } else if (status.accepted > 0) {
      parts.push(`${status.accepted} of ${status.input_samples} accepted`);
    }
    const failed = failedCount(status);
    if (failed > 0) {
      const stages = [
        [status.preparation_failed, 'failed preparation'],
        [status.verification_failed, 'failed verification'],
        [status.inference_aborted, 'aborted in inference'],
      ] as const;
      parts.push(
        stages
          .filter(([count]) => (count ?? 0) > 0)
          .map(([count, what]) => `${count} ${what}`)
          .join(', '),
      );
      // A run where nothing got through is red whatever the server calls it.
      if (light === 'bg-slate-400' && failed >= status.input_samples) {
        light = 'bg-red-500';
      }
    }
    return { label, light, detail: parts.join(' · ') };
  });

  /** Why samples failed, in the server's words (e.g. "Verification GOLDEN_IMAGE_NOT_FOUND: 47
   * sample(s) (e.g. S000002)"), shown under the status so a failed run explains itself. */
  protected readonly failureReasons: Signal<string[]> = computed(() => {
    if (failedCount(this.workService.status()) === 0) {
      return [];
    }
    const errors = this.workService.result()?.errors;
    return Array.isArray(errors) ? errors.map(String).slice(0, 4) : [];
  });

  /** Accepted against needs-review as shares of everything the agent decided on. */
  protected readonly outcomeBar: Signal<{
    total: number;
    accepted: number;
    review: number;
    acceptedShare: number;
    reviewShare: number;
  }> = computed(() => {
    const { accepted, review_required: review } = this.workService.status();
    const total = accepted + review;
    return {
      total,
      accepted,
      review,
      acceptedShare: total === 0 ? 0 : (accepted / total) * 100,
      reviewShare: total === 0 ? 0 : (review / total) * 100,
    };
  });

  /** What the agent is doing, as one word and a light - so the state is visible at a glance. */
  protected readonly runState: Signal<{ key: string; label: string; pill: string; light: string }> = computed(() => {
    const idle = {
      key: 'idle',
      label: 'Ready',
      pill: 'border-slate-300 text-slate-700 dark:border-slate-700 dark:text-slate-300',
      light: 'bg-slate-400',
    };
    if (this.uploading()) {
      return {
        key: 'uploading',
        label: 'Uploading files',
        pill: 'border-amber-300 text-amber-800 dark:border-amber-700 dark:text-amber-200',
        light: 'bg-amber-500 animate-pulse',
      };
    }
    if (this.workService.running()) {
      return {
        key: 'running',
        label: 'Running',
        pill: 'border-sky-300 text-sky-800 dark:border-sky-700 dark:text-sky-200',
        light: 'bg-sky-500 animate-pulse',
      };
    }
    const log = this.workService.log();
    if (this.uploadError() !== null || log[log.length - 1]?.kind === 'error') {
      return {
        key: 'error',
        label: 'Failed',
        pill: 'border-red-300 text-red-800 dark:border-red-700 dark:text-red-200',
        light: 'bg-red-500',
      };
    }
    if (this.workService.result() !== null) {
      return this.pendingCount() > 0
        ? {
            key: 'review',
            label: `${this.pendingCount()} to review`,
            pill: 'border-amber-300 text-amber-800 dark:border-amber-700 dark:text-amber-200',
            light: 'bg-amber-500',
          }
        : {
            key: 'done',
            label: 'Complete',
            pill: 'border-emerald-300 text-emerald-800 dark:border-emerald-700 dark:text-emerald-200',
            light: 'bg-emerald-500',
          };
    }
    return idle;
  });

  protected readonly hasNextPending: Signal<boolean> = computed(() => {
    const selected = this.selectedCaseId();
    return this.reviewCases().some((c) => c.decision === null && c.sample_id !== selected);
  });

  protected readonly imageRootSummary: Signal<string> = computed(() => {
    const files = this.imageRootFiles();
    if (files.length === 0) {
      return '';
    }
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    return `${files.length} file${files.length === 1 ? '' : 's'} · ${formatBytes(totalBytes)}`;
  });

  protected readonly runId: Signal<string | null> = computed(
    () => this.workService.result()?.run_id ?? null,
  );

  protected readonly selectedCase: Signal<WorkflowReviewCaseOut | null> = computed(() => {
    const id = this.selectedCaseId();
    return this.reviewCases().find((reviewCase) => reviewCase.sample_id === id) ?? null;
  });

  protected readonly filteredCases: Signal<WorkflowReviewCaseOut[]> = computed(() => {
    const filter = this.caseFilter();
    return this.reviewCases().filter(
      (reviewCase) =>
        filter === 'all' || (filter === 'reviewed') === (reviewCase.decision !== null),
    );
  });

  /** Why the queue is empty - the Open Review Console button always opens, so the popup has to
   * say what is missing instead of the button silently doing nothing. */
  protected readonly emptyMessage: Signal<string> = computed(() => {
    if (this.reviewCases().length > 0) {
      return 'Nothing to show for this filter.';
    }
    if (this.runId() === null) {
      return 'There is no run to review yet - run the agentic workflow first.';
    }
    return (
      'No samples in this run need review. Only samples flagged REVIEW_REQUIRED are sent to ' +
      'Agent 2 - check the status line and log for why none were.'
    );
  });

  protected readonly pendingCount: Signal<number> = computed(
    () => this.reviewCases().filter((reviewCase) => reviewCase.decision === null).length,
  );

  protected readonly reviewedCount: Signal<number> = computed(
    () => this.reviewCases().filter((reviewCase) => reviewCase.decision !== null).length,
  );

  // Mirrors ui.py's one-time `os.getenv("OPENAI_API_KEY")` check next to its "Use real LLM
  // Planner" checkbox - null while the initial GET /api/orchestrator/status is in flight (or if
  // it fails, e.g. orchestrator_agent_enabled is off), so the label only appears once it's known.
  protected readonly llmConfigured = signal<boolean | null>(null);

  // Only the newest cases / drift request may write its answer: a slow earlier one (or one for a
  // run that has since been replaced) would otherwise overwrite fresher data.
  private casesRequest = 0;
  private driftRequest = 0;
  private destroyed = false;
  private consoleOpener: HTMLElement | null = null;
  private readonly consoleDialog = viewChild<ElementRef<HTMLElement>>('consoleDialog');

  /** One line of the active policy and planner choices, so they stay visible with the panel closed. */
  protected readonly settingsSummary: Signal<string> = computed(() => {
    const s = this.workSettings;
    const planner = s.useLlm() ? `LLM planner (${s.llmModel() || 'default model'})` : 'deterministic planner';
    return `Thresholds ${s.featureThreshold().toFixed(2)} feature / ${s.defectThreshold().toFixed(2)} defect, ${planner}`;
  });

  constructor(
    protected readonly workService: WorkService,
    protected readonly workSettings: WorkSettingsService,
  ) {
    // Move focus into the Review Console when it opens, so keyboard and screen reader users land in it.
    afterRenderEffect(() => {
      const dialog = this.consoleDialog()?.nativeElement;
      if (dialog && !dialog.contains(document.activeElement)) {
        dialog.focus();
      }
    });

    // The Review Console follows the run: its cases exist as soon as the result arrives (Agent 2
    // still pending), and are refetched whenever Agent 2 finishes another sample.
    effect(() => {
      this.workService.reviewTick();
      const runId = this.runId();
      untracked(() => {
        if (runId === null) {
          this.resetReviews();
        } else {
          void this.refreshCases();
          void this.refreshDrift();
        }
      });
    });

    this.workService
      .getStatus()
      .then((status) => this.llmConfigured.set(status.llm_configured))
      .catch(() => undefined);
  }

  onDatasetFileChange(event: Event): void {
    this.datasetFile.set((event.target as HTMLInputElement).files?.[0] ?? null);
  }

  onXmlFileChange(event: Event): void {
    this.xmlFile.set((event.target as HTMLInputElement).files?.[0] ?? null);
  }

  onImageRootFilesChange(event: Event): void {
    const files = (event.target as HTMLInputElement).files;
    this.imageRootFiles.set(files ? Array.from(files) : []);
  }

  async prepare(): Promise<void> {
    await this.runMode('prepare');
  }

  async prepareAndVerify(): Promise<void> {
    await this.runMode('prepare_verify');
  }

  async runFull(): Promise<void> {
    await this.runMode('run_full');
  }

  clearLog(): void {
    this.workService.clearLog();
    this.resetReviews();
    this.actionError.set(null);
    this.actionMessage.set(null);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.revokeImages();
  }

  /** Opens the Review Console popup (the source app's "Open Review Console" button), refetching
   * first so it never shows a stale queue, and selecting the first pending case. */
  async openConsole(): Promise<void> {
    if (!this.consoleOpen()) {
      this.consoleOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    this.consoleOpen.set(true);
    await Promise.all([this.refreshCases(), this.refreshDrift()]);
    if (this.selectedCase() === null) {
      const first = this.reviewCases().find((c) => c.decision === null) ?? this.reviewCases()[0];
      if (first) {
        await this.selectCase(first.sample_id);
      }
    }
  }

  @HostListener('document:keydown.escape')
  closeConsole(): void {
    // Closing mid-save would hide the outcome of the decision being written.
    if (!this.consoleOpen() || this.reviewBusy()) {
      return;
    }
    this.consoleOpen.set(false);
    this.consoleOpener?.focus();
    this.consoleOpener = null;
  }

  /** Keeps Tab inside the open console, so focus cannot wander to the page behind the backdrop. */
  trapConsoleFocus(keyEvent: Event): void {
    const event = keyEvent as KeyboardEvent;
    const dialog = this.consoleDialog()?.nativeElement;
    if (event.key !== 'Tab' || !dialog) {
      return;
    }
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async refreshCases(): Promise<void> {
    const runId = this.runId();
    if (runId === null) {
      return;
    }
    const request = ++this.casesRequest;
    this.casesLoading.set(true);
    try {
      const cases = await this.workService.getReviewCases(runId);
      if (request === this.casesRequest && runId === this.runId()) {
        this.reviewCases.set(cases);
        this.reviewError.set(null);
      }
    } catch (error) {
      if (request === this.casesRequest) {
        this.reviewError.set(error instanceof Error ? error.message : 'Could not load review cases.');
      }
    } finally {
      if (request === this.casesRequest) {
        this.casesLoading.set(false);
      }
    }
  }

  /** Refetches the server's drift numbers and corrections for the run - the Corrections & Retraining
   * tab follows every decision the operator saves in the Explanation Review. */
  async refreshDrift(): Promise<void> {
    const runId = this.runId();
    if (runId === null) {
      return;
    }
    const request = ++this.driftRequest;
    this.driftLoading.set(true);
    try {
      const drift = await this.workService.getRunDrift(runId);
      if (request === this.driftRequest && runId === this.runId()) {
        this.runDrift.set(drift);
        this.driftError.set(null);
      }
    } catch (error) {
      if (request === this.driftRequest) {
        this.driftError.set(error instanceof Error ? error.message : 'Could not load the drift numbers.');
      }
    } finally {
      if (request === this.driftRequest) {
        this.driftLoading.set(false);
      }
    }
  }

  async showDriftTab(): Promise<void> {
    this.consoleTab.set('drift');
    await this.refreshDrift();
  }

  isCorrectionSelected(sampleId: string): boolean {
    return !this.excludedCorrectionIds().has(sampleId);
  }

  toggleCorrection(sampleId: string): void {
    this.excludedCorrectionIds.update((ids) => {
      const next = new Set(ids);
      if (!next.delete(sampleId)) {
        next.add(sampleId);
      }
      return next;
    });
  }

  /** Unticks every correction that can still be sent, or ticks them all again when none is. */
  toggleAllCorrections(): void {
    this.excludedCorrectionIds.set(
      this.allCorrectionsSelected() ? new Set(this.pendingCorrections().map((c) => c.sample_id)) : new Set(),
    );
  }

  /** Sends the ticked corrections for retraining: one retraining ticket each (built on the server
   * from the stored sample and final decision - only the ids go over the wire), then a retraining
   * plan for each model involved, so an Admin can approve it in the Models tab. */
  async sendForRetraining(): Promise<void> {
    const runId = this.runId();
    if (runId === null || !this.canQueueCorrections()) {
      return;
    }
    const selected = this.selectedCorrections();
    const models = [...new Set(selected.map((c) => c.model_name).filter((m): m is string => m !== null))];
    this.monitoringBusy.set(true);
    this.actionError.set(null);
    this.actionMessage.set(null);
    try {
      const out = await this.workService.queueCorrections({
        run_id: runId,
        sample_ids: selected.map((c) => c.sample_id),
      });
      const sent = out.created.length;
      const planned: string[] = [];
      const unplanned: string[] = [];
      for (const model of models) {
        try {
          await this.workService.draftRetrainingPlan(model);
          planned.push(model);
        } catch {
          unplanned.push(model);
        }
      }
      this.actionMessage.set(
        `Sent ${sent} correction${sent === 1 ? '' : 's'} for retraining.` +
          (planned.length > 0
            ? ` The retraining plan for ${planned.join(', ')} is waiting for an Admin to approve it in the Models tab.`
            : '') +
          (unplanned.length > 0
            ? ` No plan could be created for ${unplanned.join(', ')} yet - use "Create retraining plan" below.`
            : ''),
      );
      await this.refreshDrift();
    } catch (error) {
      this.actionError.set(
        error instanceof Error ? error.message : 'Could not send the corrections for retraining.',
      );
    } finally {
      this.monitoringBusy.set(false);
    }
  }

  protected readonly labelText = labelText;
  protected readonly sourceLabel = sourceLabel;
  protected readonly modelSaid = modelSaid;
  protected readonly trainingStatus = trainingStatus;
  protected readonly percent = percent;

  /** Drafts a retraining plan for a model from its open tickets. The plan is a job awaiting an
   * Admin's approval in the Models tab - nothing is retrained from here. */
  async draftPlan(modelName: string): Promise<void> {
    if (this.planBusy()) {
      return;
    }
    this.planBusy.set(true);
    this.actionError.set(null);
    this.actionMessage.set(null);
    try {
      const plan = await this.workService.draftRetrainingPlan(modelName);
      this.actionMessage.set(
        `Plan drafted for ${plan.model_name}: ${plan.sample_count} sample${plan.sample_count === 1 ? '' : 's'}, ` +
          `${plan.status.replace('_', ' ')}. An Admin approves it in the Models tab.`,
      );
      await this.refreshDrift();
    } catch (error) {
      this.actionError.set(error instanceof Error ? error.message : 'Could not draft the plan.');
    } finally {
      this.planBusy.set(false);
    }
  }

  /** Arrow keys in the queue: selects the neighbouring row and keeps focus on it. */
  async moveSelection(step: 1 | -1): Promise<void> {
    const cases = this.filteredCases();
    const index = cases.findIndex((c) => c.sample_id === this.selectedCaseId());
    const next = cases[Math.min(Math.max(index + step, 0), cases.length - 1)];
    if (!next || next.sample_id === this.selectedCaseId()) {
      return;
    }
    const selecting = this.selectCase(next.sample_id);
    queueMicrotask(() => {
      const row = document.querySelector<HTMLElement>(`[data-testid="case-${next.sample_id}"]`);
      row?.focus();
      row?.scrollIntoView?.({ block: 'nearest' });
    });
    await selecting;
  }

  /** Opens one case in the console's detail pane, pre-filling the form from a saved decision. */
  async selectCase(sampleId: string): Promise<void> {
    const reviewCase = this.reviewCases().find((c) => c.sample_id === sampleId);
    if (!reviewCase) {
      return;
    }
    this.selectedCaseId.set(sampleId);
    this.reviewError.set(null);
    this.reviewMessage.set(null);
    const decision = reviewCase.decision;
    this.decisionSource.set(decision?.selected_source ?? 'MACHINE');
    this.manualClass.set(decision?.selected_source === 'MANUAL' ? decision.final_result : IPC_CLASSES[0]);
    this.operatorNotes.set(decision?.operator_notes ?? '');
    await this.loadImages(reviewCase);
  }

  /** Saves the operator's call: Agent 1's verdict (MACHINE), Agent 2's (AI), or an IPC class
   * (MANUAL). Only possible for AI once Agent 2 has actually reviewed the case. */
  async submitDecision(advance = false): Promise<void> {
    const reviewCase = this.selectedCase();
    if (reviewCase === null || this.reviewBusy()) {
      return;
    }
    const source = this.decisionSource();
    const agent2Verdict = reviewCase.review?.agent2_verdict ?? null;
    if (source === 'AI' && agent2Verdict === null) {
      return;
    }
    const finalResult =
      source === 'MACHINE'
        ? reviewCase.agent1_verdict
        : source === 'AI'
          ? (agent2Verdict ?? '')
          : this.manualClass();

    this.reviewBusy.set(true);
    this.reviewError.set(null);
    this.reviewMessage.set(null);
    try {
      await this.workService.saveReviewDecision({
        run_id: reviewCase.run_id,
        sample_id: reviewCase.sample_id,
        selected_source: source,
        final_result: finalResult,
        machine_result: reviewCase.agent1_verdict,
        ai_result: agent2Verdict,
        ai_diagnosis: reviewCase.review?.diagnosis ?? null,
        operator_notes: this.operatorNotes().trim() || null,
      });
      this.reviewMessage.set(`Saved ${reviewCase.sample_id}: ${finalResult}`);
      await Promise.all([this.refreshCases(), this.refreshDrift()]);
      if (advance) {
        const next = this.reviewCases().find(
          (c) => c.decision === null && c.sample_id !== reviewCase.sample_id,
        );
        if (next) {
          await this.selectCase(next.sample_id);
          this.reviewMessage.set(`Saved ${reviewCase.sample_id}: ${finalResult}`);
        }
      }
    } catch (error) {
      this.reviewError.set(error instanceof Error ? error.message : 'Could not save the decision.');
    } finally {
      this.reviewBusy.set(false);
    }
  }

  private async loadImages(reviewCase: WorkflowReviewCaseOut): Promise<void> {
    this.revokeImages();
    this.imagesLoading.set(true);
    const [golden, defect] = await Promise.all([
      this.fetchImage(reviewCase, 'golden', reviewCase.has_golden_image),
      this.fetchImage(reviewCase, 'defect', reviewCase.has_defect_image),
    ]);
    if (this.selectedCaseId() === reviewCase.sample_id) {
      this.imagesLoading.set(false);
    }
    if (this.destroyed || this.selectedCaseId() !== reviewCase.sample_id) {
      // The operator moved on (or left) while these downloaded - do not leak or show them.
      for (const url of [golden, defect]) {
        if (url !== null) {
          URL.revokeObjectURL(url);
        }
      }
      return;
    }
    this.goldenImageUrl.set(golden);
    this.defectImageUrl.set(defect);
  }

  private async fetchImage(
    reviewCase: WorkflowReviewCaseOut,
    kind: 'golden' | 'defect',
    available: boolean,
  ): Promise<string | null> {
    if (!available) {
      return null;
    }
    try {
      return await this.workService.getReviewImageUrl(reviewCase.run_id, reviewCase.sample_id, kind);
    } catch {
      return null; // a missing crop should not block the decision
    }
  }

  private revokeImages(): void {
    for (const url of [this.goldenImageUrl(), this.defectImageUrl()]) {
      if (url !== null) {
        URL.revokeObjectURL(url);
      }
    }
    this.goldenImageUrl.set(null);
    this.defectImageUrl.set(null);
  }

  private resetReviews(): void {
    this.reviewCases.set([]);
    this.consoleOpen.set(false);
    this.selectedCaseId.set(null);
    this.reviewError.set(null);
    this.reviewMessage.set(null);
    this.runDrift.set(null);
    this.driftError.set(null);
    this.actionError.set(null);
    this.actionMessage.set(null);
    this.excludedCorrectionIds.set(new Set());
    this.revokeImages();
  }

  private async runMode(mode: OrchestratorRunMode): Promise<void> {
    const dataset = this.datasetFile();
    const xml = this.xmlFile();
    if (!dataset || !xml) {
      return;
    }

    this.uploadError.set(null);
    this.resetReviews();
    this.uploading.set(true);
    try {
      const datasetId = await this.workService.uploadDataset(dataset);
      const xmlId = await this.workService.uploadXml(xml);
      const imageRootFiles = this.imageRootFiles();
      const imageRootId = imageRootFiles.length
        ? await this.workService.uploadImageRootFiles(imageRootFiles)
        : null;

      this.workService.run(mode, datasetId, xmlId, imageRootId, {
        featureThreshold: this.workSettings.featureThreshold(),
        defectThreshold: this.workSettings.defectThreshold(),
        useLlm: this.workSettings.useLlm(),
        llmModel: this.workSettings.llmModel(),
        llmFallback: this.workSettings.llmFallback(),
      });
    } catch (error) {
      this.uploadError.set(error instanceof Error ? error.message : 'Upload failed.');
    } finally {
      this.uploading.set(false);
    }
  }
}
