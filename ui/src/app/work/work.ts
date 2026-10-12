import {
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
import {
  OrchestratorInferenceResult,
  OrchestratorRunMode,
  WorkflowDecisionSource,
  WorkflowCorrection,
  WorkflowReviewCaseOut,
  WorkflowRunDrift,
} from './models/orchestrator.models';
import { formatLog, logEntryText, logTime } from './work-log';
import { WorkSettingsService } from './work-settings.service';
import { WorkSettings } from './work-settings/work-settings';
import { WorkService } from './work.service';

/** How many log lines are drawn at once. The full log is always kept (and downloadable); drawing
 * thousands of rows on every new line is what makes a long bulk run sluggish. */
export const LOG_RENDER_LIMIT = 500;

/** A pane counts as "at the bottom" within this many pixels, so a fractional scroll position
 * doesn't stop it following. */
const LOG_BOTTOM_SLACK_PX = 24;

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
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
  imports: [FormsModule, WorkSettings],
  selector: 'app-work',
  styleUrl: './work.css',
  templateUrl: './work.html',
})
export class Work implements OnDestroy {
  protected readonly datasetFile = signal<File | null>(null);
  protected readonly xmlFile = signal<File | null>(null);
  protected readonly imageRootFiles = signal<File[]>([]);
  protected readonly outputFilename = signal('result.json');

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
  /** Corrections the operator ticked - nothing is sent unless it was chosen. */
  protected readonly chosenCorrectionIds = signal<ReadonlySet<string>>(new Set());

  protected readonly corrections: Signal<WorkflowCorrection[]> = computed(
    () => this.runDrift()?.corrections ?? [],
  );
  /** Corrections that can still be sent: they have a model to train and no ticket yet. */
  protected readonly pendingCorrections: Signal<WorkflowCorrection[]> = computed(() =>
    this.corrections().filter((c) => c.queueable && !c.queued),
  );
  protected readonly selectedCorrections: Signal<WorkflowCorrection[]> = computed(() =>
    this.pendingCorrections().filter((c) => this.chosenCorrectionIds().has(c.sample_id)),
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
  /** The flagged samples split into one bar: model right / wrong / couldn't decide / not reviewed. */
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
      { key: 'no-answer', label: "Couldn't decide", count: drift.totals.no_prediction, barClass: 'bg-amber-400' },
      { key: 'pending', label: 'Not reviewed yet', count: drift.decisions.pending, barClass: 'bg-slate-300 dark:bg-slate-600' },
    ];
    const total = parts.reduce((sum, part) => sum + part.count, 0);
    return parts.map((part) => ({ ...part, share: total === 0 ? 0 : (part.count / total) * 100 }));
  });
  protected readonly retrainingSteps = [
    { n: 1, title: 'Review flagged samples', hint: 'Explanation Review tab' },
    { n: 2, title: 'Send your corrections for retraining', hint: 'this tab' },
    { n: 3, title: 'An Admin approves the plan', hint: 'Models tab' },
  ] as const;
  /** Which of the three steps the operator is on: review, send corrections, wait for the Admin. */
  protected readonly retrainingStep: Signal<1 | 2 | 3> = computed(() => {
    if (this.pendingCorrections().length > 0) {
      return 2;
    }
    return this.corrections().some((c) => c.queued) ? 3 : 1;
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
  protected readonly reviewError = signal<string | null>(null);
  protected readonly reviewMessage = signal<string | null>(null);

  protected readonly canRun: Signal<boolean> = computed(
    () => this.datasetFile() !== null && this.xmlFile() !== null && !this.uploading() && !this.workService.running(),
  );

  protected readonly hasRun: Signal<boolean> = computed(() => this.workService.log().length > 0);

  // The execution log pane: follows new lines until the operator scrolls up, and can be filtered.
  protected readonly logFilter = signal<'all' | 'steps' | 'errors'>('all');
  protected readonly logFollowing = signal(true);
  /** How long the log was when the operator scrolled away from the bottom; null while following. */
  private readonly logPausedAt = signal<number | null>(null);
  protected readonly logNotice = signal<string | null>(null);
  private readonly logPane = viewChild<ElementRef<HTMLElement>>('logPane');
  protected readonly logTime = logTime;
  protected readonly logEntryText = logEntryText;

  private readonly filteredLog = computed(() => {
    const filter = this.logFilter();
    const entries = this.workService.log();
    if (filter === 'all') {
      return entries;
    }
    // Errors are always worth seeing, so the plan-step view keeps them.
    return entries.filter((entry) =>
      filter === 'errors' ? entry.kind === 'error' : entry.kind !== 'log',
    );
  });
  protected readonly visibleLog = computed(() => this.filteredLog().slice(-LOG_RENDER_LIMIT));
  protected readonly hiddenLogCount: Signal<number> = computed(
    () => this.filteredLog().length - this.visibleLog().length,
  );
  /** Lines that arrived while the operator was reading further up. */
  protected readonly unseenLogCount: Signal<number> = computed(() => {
    const pausedAt = this.logPausedAt();
    return pausedAt === null ? 0 : Math.max(this.workService.log().length - pausedAt, 0);
  });

  protected readonly imageRootSummary: Signal<string> = computed(() => {
    const files = this.imageRootFiles();
    if (files.length === 0) {
      return '';
    }
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    return `${files.length} file${files.length === 1 ? '' : 's'} · ${formatBytes(totalBytes)}`;
  });

  protected readonly resultJson: Signal<string | null> = computed(() => {
    const result = this.workService.result();
    return result === null ? null : JSON.stringify(result, null, 2);
  });

  protected readonly results: Signal<OrchestratorInferenceResult[]> = computed(
    () => this.workService.result()?.results ?? [],
  );

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
    // Keep the newest line in view while following - after render, so the new row has a height.
    afterRenderEffect(() => {
      this.visibleLog();
      const pane = this.logPane()?.nativeElement;
      if (pane && untracked(() => this.logFollowing())) {
        pane.scrollTop = pane.scrollHeight;
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
      .catch(() => this.llmConfigured.set(null));
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

  /** Stops following when the operator scrolls up, and resumes when they reach the bottom again. */
  onLogScroll(): void {
    const pane = this.logPane()?.nativeElement;
    if (!pane) {
      return;
    }
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < LOG_BOTTOM_SLACK_PX;
    if (atBottom) {
      this.logFollowing.set(true);
      this.logPausedAt.set(null);
    } else if (this.logFollowing()) {
      this.logFollowing.set(false);
      this.logPausedAt.set(this.workService.log().length);
    }
  }

  jumpToLatestLog(): void {
    this.logFollowing.set(true);
    this.logPausedAt.set(null);
    const pane = this.logPane()?.nativeElement;
    if (pane) {
      pane.scrollTop = pane.scrollHeight;
    }
  }

  setLogFilter(filter: 'all' | 'steps' | 'errors'): void {
    this.logFilter.set(filter);
    this.jumpToLatestLog();
  }

  /** Saves the whole log (not just the lines drawn) as a text file. */
  downloadLog(): void {
    const entries = this.workService.log();
    if (entries.length === 0) {
      return;
    }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    this.saveFile(
      new Blob([formatLog(entries)], { type: 'text/plain' }),
      `execution-log-${this.runId() ?? 'run'}-${stamp}.txt`,
    );
  }

  async copyLog(): Promise<void> {
    const entries = this.workService.log();
    if (entries.length === 0) {
      return;
    }
    try {
      await navigator.clipboard.writeText(formatLog(entries));
      this.logNotice.set(`Copied ${entries.length} line${entries.length === 1 ? '' : 's'}`);
    } catch {
      this.logNotice.set('Could not copy - use Download log instead');
    }
    setTimeout(() => this.logNotice.set(null), 2500);
  }

  clearLog(): void {
    this.workService.clearLog();
    this.logFollowing.set(true);
    this.logPausedAt.set(null);
    this.resetReviews();
    this.actionError.set(null);
    this.actionMessage.set(null);
  }

  ngOnDestroy(): void {
    this.revokeImages();
  }

  /** Opens the Review Console popup (the source app's "Open Review Console" button), refetching
   * first so it never shows a stale queue, and selecting the first pending case. */
  async openConsole(): Promise<void> {
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
    this.consoleOpen.set(false);
  }

  async refreshCases(): Promise<void> {
    const runId = this.runId();
    if (runId === null) {
      return;
    }
    try {
      this.reviewCases.set(await this.workService.getReviewCases(runId));
      this.reviewError.set(null);
    } catch (error) {
      this.reviewError.set(error instanceof Error ? error.message : 'Could not load review cases.');
    }
  }

  /** Refetches the server's drift numbers and corrections for the run - the Corrections & Retraining
   * tab follows every decision the operator saves in the Explanation Review. */
  async refreshDrift(): Promise<void> {
    const runId = this.runId();
    if (runId === null) {
      return;
    }
    this.driftLoading.set(true);
    try {
      this.runDrift.set(await this.workService.getRunDrift(runId));
      this.driftError.set(null);
    } catch (error) {
      this.driftError.set(error instanceof Error ? error.message : 'Could not load the drift numbers.');
    } finally {
      this.driftLoading.set(false);
    }
  }

  async showDriftTab(): Promise<void> {
    this.consoleTab.set('drift');
    await this.refreshDrift();
  }

  isCorrectionSelected(sampleId: string): boolean {
    return this.chosenCorrectionIds().has(sampleId);
  }

  toggleCorrection(sampleId: string): void {
    this.chosenCorrectionIds.update((ids) => {
      const next = new Set(ids);
      if (!next.delete(sampleId)) {
        next.add(sampleId);
      }
      return next;
    });
  }

  /** Ticks every correction that can still be sent, or clears the ticks when all are ticked. */
  toggleAllCorrections(): void {
    this.chosenCorrectionIds.set(
      this.allCorrectionsSelected() ? new Set() : new Set(this.pendingCorrections().map((c) => c.sample_id)),
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

  /** A defect label as the Explanation Review shows it: 'MissingPart_3' -> 'missing part'. Display
   * only - the same canonical form app/shared/modelops/run_drift.py's normalize_defect produces. */
  labelText(label: string | null | undefined): string {
    if (!label) {
      return '—';
    }
    const spaced = label
      .trim()
      .split('_')[0]
      .replace(/(?<!^)(?=[A-Z])/g, ' ')
      .toLowerCase()
      .replace(/-/g, ' ')
      .split(/\s+/)
      .join(' ')
      .replace('insuffcient', 'insufficient');
    return spaced === 'golden' ? 'no defect' : spaced;
  }

  /** The Explanation Review's own names for how a final decision was made. */
  sourceLabel(source: string | null | undefined): string {
    const labels: Record<string, string> = { MACHINE: 'Accept Machine', AI: 'Accept AI', MANUAL: 'Manual' };
    return source ? (labels[source] ?? source) : '—';
  }

  /** What the model said for a sample it didn't get right: its prediction, or that it couldn't
   * decide with the region model's best guess. */
  modelSaid(correction: WorkflowCorrection): string {
    if (correction.agent1_label !== null) {
      return this.labelText(correction.agent1_label);
    }
    const guess = correction.region_guess;
    if (!guess) {
      return "couldn't decide";
    }
    const confidence = guess.confidence === null ? '' : ` ${Math.round(guess.confidence * 100)}%`;
    return `couldn't decide (first guess: ${guess.label} area${confidence})`;
  }

  /** Where a correction stands on its way to retraining. */
  trainingStatus(correction: WorkflowCorrection): string {
    if (!correction.queueable && !correction.queued) {
      return "Can't send: no defect model to route it to";
    }
    switch (correction.ticket_status) {
      case 'open':
        return 'Sent, waiting for a retraining plan';
      case 'acknowledged':
        return 'In a retraining plan, awaiting Admin approval';
      case 'resolved':
        return 'Retrained';
      default:
        return 'Not sent yet';
    }
  }

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
  async submitDecision(): Promise<void> {
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
    } catch (error) {
      this.reviewError.set(error instanceof Error ? error.message : 'Could not save the decision.');
    } finally {
      this.reviewBusy.set(false);
    }
  }

  private async loadImages(reviewCase: WorkflowReviewCaseOut): Promise<void> {
    this.revokeImages();
    const [golden, defect] = await Promise.all([
      this.fetchImage(reviewCase, 'golden', reviewCase.has_golden_image),
      this.fetchImage(reviewCase, 'defect', reviewCase.has_defect_image),
    ]);
    if (this.selectedCaseId() !== reviewCase.sample_id) {
      // The operator moved on while these downloaded - do not leak or show them.
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
    this.chosenCorrectionIds.set(new Set());
    this.revokeImages();
  }

  /** 0.5 -> "50%"; "—" when there is nothing to measure. */
  percent(value: number | null): string {
    return value === null ? '—' : `${Math.round(value * 100)}%`;
  }

  downloadResult(): void {
    const json = this.resultJson();
    if (json === null) {
      return;
    }
    this.saveFile(
      new Blob([json], { type: 'application/json' }),
      this.outputFilename().trim() || 'result.json',
    );
  }

  private saveFile(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    // Revoking on the same tick races the browser actually starting the download - some
    // browsers haven't read the blob yet, so the save can silently fail or produce a truncated
    // file. Deferring a tick gives the download a chance to begin first.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
