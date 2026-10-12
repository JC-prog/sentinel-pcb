import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { Work } from './work';
import { WorkService } from './work.service';
import { WorkSettingsService } from './work-settings.service';
import {
  OrchestratorInferenceResult,
  OrchestratorLogEntry,
  OrchestratorRunResult,
  OrchestratorStatusEvent,
  WorkflowCorrection,
  WorkflowReviewCaseOut,
  WorkflowReviewOut,
  WorkflowRunDrift,
} from './models/orchestrator.models';

const IDLE_STATUS: OrchestratorStatusEvent = {
  status: 'Ready',
  input_samples: 0,
  preparation_ready: 0,
  verification_passed: 0,
  inference_attempted: 0,
  accepted: 0,
  review_required: 0,
};

function correction(overrides: Partial<WorkflowCorrection> = {}): WorkflowCorrection {
  return {
    sample_id: 'S1',
    model_name: 'pcb_body_defect',
    model_version: 'JcProg/body@v2',
    agent1_label: 'MissingPart',
    machine_label: 'WrongPart_13',
    agent2_label: 'tombstone',
    final_result: 'tombstone',
    selected_source: 'MANUAL',
    operator_notes: 'bare pads',
    outcome: 'relabelled',
    model_source: 'classified',
    region_guess: { label: 'Body', confidence: 0.95 },
    queueable: true,
    queued: false,
    ticket_status: null,
    ...overrides,
  };
}

function runDrift(
  corrections: WorkflowCorrection[] = [],
  overrides: Partial<WorkflowRunDrift> = {},
): WorkflowRunDrift {
  return {
    run_id: 'run-1',
    available: true,
    totals: {
      samples: 2,
      review_required: 2,
      decided: corrections.length,
      confirmed: 0,
      relabelled: corrections.length,
      no_prediction: 0,
      corrected: corrections.length,
    },
    drift_rate: corrections.length > 0 ? 1 : null,
    models: [
      {
        model_name: 'pcb_body_defect',
        model_version: 'JcProg/body@v2',
        samples: 2,
        review_required: 2,
        decided: corrections.length,
        confirmed: 0,
        relabelled: corrections.length,
        no_prediction: 0,
        corrected: corrections.length,
        correction_rate: corrections.length > 0 ? 0.5 : null,
        agent2_disagreed: 1,
        low_confidence_rate: 0.5,
        mean_confidence: 0.7,
      },
    ],
    corrections,
    decisions: {
      by_source: {
        MACHINE: { chosen: 0, overruled_model: 0 },
        AI: { chosen: 0, overruled_model: 0 },
        MANUAL: { chosen: corrections.length, overruled_model: corrections.length },
      },
      pending: Math.max(2 - corrections.length, 0),
      top_overruled: corrections.length
        ? [{ model_label: 'missing part', chosen_label: 'tombstone', count: corrections.length }]
        : [],
    },
    ...overrides,
  };
}

function fakeWorkService(): {
  service: WorkService;
  log: ReturnType<typeof signal<OrchestratorLogEntry[]>>;
  result: ReturnType<typeof signal<OrchestratorRunResult | null>>;
  status: ReturnType<typeof signal<OrchestratorStatusEvent>>;
  run: ReturnType<typeof vi.fn>;
  clearLog: ReturnType<typeof vi.fn>;
  getReviewCases: ReturnType<typeof vi.fn>;
  getReviewImageUrl: ReturnType<typeof vi.fn>;
  saveReviewDecision: ReturnType<typeof vi.fn>;
  getRunDrift: ReturnType<typeof vi.fn>;
  queueCorrections: ReturnType<typeof vi.fn>;
  draftRetrainingPlan: ReturnType<typeof vi.fn>;
  reviewTick: ReturnType<typeof signal<number>>;
} {
  const log = signal<OrchestratorLogEntry[]>([]);
  const result = signal<OrchestratorRunResult | null>(null);
  const run = vi.fn();
  const clearLog = vi.fn(() => {
    log.set([]);
    result.set(null);
  });
  const reviewTick = signal(0);
  const getReviewCases = vi.fn().mockResolvedValue([]);
  const getReviewImageUrl = vi
    .fn()
    .mockImplementation(async (runId: string, sampleId: string, kind: string) => `blob:${runId}-${sampleId}-${kind}`);
  const saveReviewDecision = vi.fn().mockImplementation(async (request) => ({
    ...request,
    machine_result: request.machine_result ?? null,
    ai_result: request.ai_result ?? null,
    operator_notes: request.operator_notes ?? null,
    decided_by_user_id: 'u1',
  }));

  const getRunDrift = vi.fn().mockResolvedValue(runDrift());
  const queueCorrections = vi.fn().mockResolvedValue({ created: [], already_queued: [] });
  const draftRetrainingPlan = vi.fn().mockResolvedValue({
    job_id: 'j1',
    model_name: 'pcb_body_defect',
    status: 'pending_approval',
    base_version: 'JcProg/body@v2',
    sample_count: 2,
    drift_reports_linked: 0,
  });

  const status = signal(IDLE_STATUS);
  const service = {
    status,
    log,
    running: signal(false),
    result,
    getStatus: vi.fn().mockResolvedValue({ llm_configured: false }),
    uploadDataset: vi.fn().mockResolvedValue('dataset-1'),
    uploadXml: vi.fn().mockResolvedValue('xml-1'),
    uploadImageRootFiles: vi.fn().mockResolvedValue('root-1'),
    run,
    clearLog,
    reviewTick,
    getReviewCases,
    getReviewImageUrl,
    saveReviewDecision,
    getRunDrift,
    queueCorrections,
    draftRetrainingPlan,
  } as unknown as WorkService;

  return {
    service,
    log,
    result,
    status,
    run,
    clearLog,
    getReviewCases,
    getReviewImageUrl,
    saveReviewDecision,
    getRunDrift,
    queueCorrections,
    draftRetrainingPlan,
    reviewTick,
  };
}

function file(name: string): File {
  return new File(['x'], name);
}

function sample(overrides: Partial<OrchestratorInferenceResult> = {}): OrchestratorInferenceResult {
  return {
    sample_id: 'S1',
    final_decision: 'REVIEW_REQUIRED',
    routing: { selected_model: 'body', service_model: 'pcb_body_defect' },
    defect_classification: { prediction: 'MissingPart', confidence: 0.6 },
    ...overrides,
  };
}

function runResult(results: OrchestratorInferenceResult[]): OrchestratorRunResult {
  return { workflow_status: 'INFERENCE_EXECUTED', termination_reason: null, results };
}

describe('Work', () => {
  let fixture: ComponentFixture<Work>;
  let fake: ReturnType<typeof fakeWorkService>;

  beforeEach(async () => {
    localStorage.clear();
    fake = fakeWorkService();
    await TestBed.configureTestingModule({
      imports: [Work],
      providers: [provideRouter([]), { provide: WorkService, useValue: fake.service }],
    }).compileComponents();

    fixture = TestBed.createComponent(Work);
    fixture.detectChanges();
  });

  function openSettings(): void {
    (fixture.nativeElement.querySelector('[data-testid="open-settings"]') as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  function runButton(): HTMLButtonElement {
    return Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLButtonElement).textContent?.includes('Run Agentic Workflow'),
    ) as HTMLButtonElement;
  }

  it('shows the OPENAI_API_KEY status label once the status call resolves', async () => {
    openSettings();
    await fixture.whenStable();
    fixture.detectChanges();

    // fakeWorkService()'s default getStatus() resolves { llm_configured: false }.
    expect(fixture.nativeElement.textContent).toContain('OPENAI_API_KEY not detected');
  });

  it('keeps the policy and planner controls off the page until Settings is opened', () => {
    const page = fixture.nativeElement as HTMLElement;
    expect(page.querySelector('[data-testid="feature-threshold"]')).toBeNull();
    expect(page.querySelector('[data-testid="settings-summary"]')?.textContent).toContain(
      'Thresholds 0.70 feature / 0.70 defect, deterministic planner',
    );

    openSettings();
    expect(page.querySelector('[data-testid="feature-threshold"]')).not.toBeNull();

    (page.querySelector('[data-testid="work-settings-backdrop"]') as HTMLElement).click();
    fixture.detectChanges();
    expect(page.querySelector('[data-testid="feature-threshold"]')).toBeNull();
  });

  it('sends the saved settings with the run', async () => {
    const settings = TestBed.inject(WorkSettingsService);
    settings.setDefectThreshold(0.85);
    settings.setUseLlm(true);
    settings.setLlmModel('gpt-x');
    fixture.componentInstance.onDatasetFileChange({ target: { files: [file('dataset.csv')] } } as unknown as Event);
    fixture.componentInstance.onXmlFileChange({ target: { files: [file('inspection.xml')] } } as unknown as Event);

    await fixture.componentInstance.runFull();

    expect(fake.run).toHaveBeenCalledWith(
      'run_full',
      'dataset-1',
      'xml-1',
      null,
      expect.objectContaining({ defectThreshold: 0.85, useLlm: true, llmModel: 'gpt-x' }),
    );
  });

  it('shows nothing when the status call fails, rather than a stale/wrong label', async () => {
    fake = fakeWorkService();
    (fake.service.getStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('offline'));
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Work],
      providers: [provideRouter([]), { provide: WorkService, useValue: fake.service }],
    }).compileComponents();
    fixture = TestBed.createComponent(Work);
    fixture.detectChanges();
    openSettings();

    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('OPENAI_API_KEY');
  });

  it('disables the run buttons until both a dataset and an XML file are selected', () => {
    expect(runButton().disabled).toBe(true);

    fixture.componentInstance.onDatasetFileChange({ target: { files: [file('dataset.csv')] } } as unknown as Event);
    fixture.detectChanges();
    expect(runButton().disabled).toBe(true);

    fixture.componentInstance.onXmlFileChange({ target: { files: [file('inspection.xml')] } } as unknown as Event);
    fixture.detectChanges();
    expect(runButton().disabled).toBe(false);
  });

  it('uploads the dataset and XML before starting a run_full run', async () => {
    fixture.componentInstance.onDatasetFileChange({ target: { files: [file('dataset.csv')] } } as unknown as Event);
    fixture.componentInstance.onXmlFileChange({ target: { files: [file('inspection.xml')] } } as unknown as Event);
    fixture.detectChanges();

    await fixture.componentInstance.runFull();

    expect(fake.service.uploadDataset).toHaveBeenCalled();
    expect(fake.service.uploadXml).toHaveBeenCalled();
    expect(fake.run).toHaveBeenCalledWith(
      'run_full',
      'dataset-1',
      'xml-1',
      null,
      expect.objectContaining({ featureThreshold: 0.7, defectThreshold: 0.7 }),
    );
  });

  it('renders log entries from the work service in order', () => {
    fake.log.set([
      { kind: 'log', text: 'hello' },
      { kind: 'error', message: 'oops' },
    ]);
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('hello');
    expect(text).toContain('oops');
  });

  it('clear log delegates to the work service only, no upload/run call', () => {
    // Unlike the run buttons, Clear Log is always available - matches the tkinter source app,
    // where it wasn't gated on anything having run yet either.
    const clearButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLButtonElement).textContent?.includes('Clear Log'),
    ) as HTMLButtonElement;

    clearButton.click();

    expect(fake.clearLog).toHaveBeenCalled();
    expect(fake.run).not.toHaveBeenCalled();
  });

  it('shows the idle empty state before any run has started', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('State → Planner → Policy → Tool → State Update → Re-plan');
  });

  it('offers the run result for download once there is one', () => {
    const download = () =>
      fixture.nativeElement.querySelector('[data-testid="download-result"]') as HTMLButtonElement;
    expect(download().disabled).toBe(true);

    fake.result.set(runResult([]));
    fixture.detectChanges();

    expect(download().disabled).toBe(false);
    expect(download().textContent).toContain('Download result');
  });

  describe('run summary', () => {
    it('words the status and splits accepted from review', () => {
      fake.status.set({
        status: 'REVIEW_REQUIRED',
        input_samples: 11,
        preparation_ready: 11,
        verification_passed: 11,
        inference_attempted: 11,
        accepted: 3,
        review_required: 8,
      });
      fixture.detectChanges();

      const status = fixture.nativeElement.querySelector('[data-testid="summary-status"]') as HTMLElement;
      expect(status.textContent?.trim()).toBe('Review required');
      expect(fixture.nativeElement.textContent).toContain('8 of 11 samples need a person to confirm');
      expect(fixture.nativeElement.querySelector('[role="img"][aria-label="3 accepted, 8 need review"]')).not.toBeNull();
    });

    const tile = (label: string): HTMLElement | undefined =>
      (Array.from(fixture.nativeElement.querySelectorAll('[role="group"] .rounded-lg.border')) as HTMLElement[]).find(
        (el) => el.querySelector('p')?.textContent?.trim() === label,
      );

    it('shows a Failed tile that stays plain while nothing has failed', () => {
      fake.status.set({ ...IDLE_STATUS, status: 'REVIEW_REQUIRED', input_samples: 11, review_required: 11 });
      fixture.detectChanges();

      const failed = tile('Failed');
      expect(failed?.textContent).toContain('0');
      expect(failed?.className).not.toContain('border-red-300');
      expect(fixture.nativeElement.querySelector('[data-testid="failure-reasons"]')).toBeNull();
    });

    it('counts samples that failed at any stage, says where, and shows the server\'s reasons', () => {
      fake.status.set({
        ...IDLE_STATUS,
        status: 'REVIEW_REQUIRED',
        input_samples: 76,
        preparation_ready: 76,
        verification_passed: 72,
        inference_attempted: 72,
        review_required: 72,
        verification_failed: 4,
      });
      fake.result.set({
        ...runResult([]),
        errors: ['Verification IMAGE_PAIR_ALIGNMENT_FAILED: 4 sample(s) (e.g. S000012)'],
      });
      fixture.detectChanges();

      const failed = tile('Failed');
      expect(failed?.textContent).toContain('4');
      expect(failed?.className).toContain('border-red-300');
      expect(fixture.nativeElement.textContent).toContain('72 of 76 samples need a person to confirm');
      expect(fixture.nativeElement.textContent).toContain('4 failed verification');
      expect(fixture.nativeElement.querySelector('[data-testid="failure-reasons"]').textContent).toContain(
        'IMAGE_PAIR_ALIGNMENT_FAILED: 4 sample(s)',
      );
    });

    it('explains an aborted run where nothing got through verification', () => {
      fake.status.set({
        ...IDLE_STATUS,
        status: 'ABORTED',
        input_samples: 76,
        preparation_ready: 76,
        verification_failed: 76,
      });
      fake.result.set({
        ...runResult([]),
        errors: ['Verification GOLDEN_IMAGE_NOT_FOUND: 76 sample(s) (e.g. S000001)'],
      });
      fixture.detectChanges();

      expect(tile('Failed')?.textContent).toContain('76');
      expect(fixture.nativeElement.textContent).toContain('76 failed verification');
      expect(fixture.nativeElement.querySelector('[data-testid="failure-reasons"]').textContent).toContain(
        'GOLDEN_IMAGE_NOT_FOUND: 76 sample(s)',
      );
    });

    it('adds up preparation, verification and inference failures', () => {
      fake.status.set({
        ...IDLE_STATUS,
        status: 'INFERENCE_COMPLETED',
        input_samples: 10,
        preparation_failed: 1,
        verification_failed: 2,
        inference_aborted: 3,
      });
      fixture.detectChanges();

      expect(tile('Failed')?.textContent).toContain('6');
      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('1 failed preparation, 2 failed verification, 3 aborted in inference');
    });
  });

  describe('status light', () => {
    const state = (): string | null =>
      fixture.nativeElement.querySelector('[data-testid="run-state"]').getAttribute('data-state');

    it('is idle before anything runs and shows the agent name', () => {
      expect(state()).toBe('idle');
      expect(fixture.nativeElement.querySelector('h1').textContent).toContain('AOI Inspection Agent');
    });

    it('turns to failed when the last log entry is an error', () => {
      fake.log.set([{ kind: 'error', message: 'inference down' }]);
      fixture.detectChanges();
      expect(state()).toBe('error');
    });

    it('shows how many samples are waiting once a run has finished', () => {
      fake.result.set({ ...runResult([]), run_id: 'run-1' });
      fixture.detectChanges();
      expect(state()).toMatch(/done|review/);
    });
  });

  describe('Execution log', () => {
    const pane = (): HTMLElement => fixture.nativeElement.querySelector('[data-testid="log-pane"]');
    const rows = (): HTMLElement[] => Array.from(pane().querySelectorAll('p[data-kind]'));
    const button = (id: string): HTMLButtonElement =>
      fixture.nativeElement.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement;
    const step = (overrides: Record<string, unknown> = {}): OrchestratorLogEntry => ({
      kind: 'plan_step',
      step: {
        plan_version: 3,
        decision: 'run_inference',
        reason: 'verified samples are ready',
        planner_source: 'llm',
        policy: null,
        ...overrides,
      },
    } as unknown as OrchestratorLogEntry);
    // 2026-10-12 09:08:07 local time, whatever the machine's zone
    const at = new Date(2026, 9, 12, 9, 8, 7).getTime();

    function setFilter(value: 'all' | 'steps' | 'errors'): void {
      const select = fixture.nativeElement.querySelector('[data-testid="log-filter"]') as HTMLSelectElement;
      select.value = value;
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    }

    function readBlob(blob: Blob): Promise<string> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(blob);
      });
    }

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('draws each kind distinctly: plan steps with their source, policy rejections flagged, errors marked', () => {
      fake.log.set([
        { kind: 'log', text: 'Run started', at },
        step({ policy: { allowed: false, reason: 'verification not run yet' } }),
        { kind: 'error', message: 'inference down' },
      ]);
      fixture.detectChanges();

      const [plain, planned, failed] = rows();
      expect(plain.textContent).toContain('09:08:07');
      expect(plain.textContent).toContain('Run started');
      expect(planned.textContent).toContain('Step 3');
      expect(planned.textContent).toContain('llm');
      expect(planned.textContent).toContain('run_inference - verified samples are ready');
      expect(planned.textContent).toContain('(policy rejected: verification not run yet)');
      expect(failed.textContent).toContain('ERROR: inference down');
    });

    it('filters to plan steps and errors, or errors only', () => {
      fake.log.set([{ kind: 'log', text: 'chatter' }, step(), { kind: 'error', message: 'bad' }]);
      fixture.detectChanges();
      expect(rows().length).toBe(3);

      setFilter('steps');
      expect(rows().map((row) => row.getAttribute('data-kind'))).toEqual(['plan_step', 'error']);

      setFilter('errors');
      expect(rows().map((row) => row.getAttribute('data-kind'))).toEqual(['error']);
    });

    it('draws only the latest lines of a long log, and says the rest is in the download', () => {
      fake.log.set(Array.from({ length: 620 }, (_, i) => ({ kind: 'log', text: `line ${i}` }) as OrchestratorLogEntry));
      fixture.detectChanges();

      expect(rows().length).toBe(500);
      expect(rows()[0].textContent).toContain('line 120');
      expect(rows()[499].textContent).toContain('line 619');
      expect(fixture.nativeElement.querySelector('[data-testid="log-hidden"]').textContent).toContain(
        'Showing the latest 500 of 620 lines',
      );
    });

    it('downloads the whole log, not only the lines drawn', async () => {
      const entries: OrchestratorLogEntry[] = [
        { kind: 'log', text: 'first', at },
        ...Array.from({ length: 600 }, (_, i) => ({ kind: 'log', text: `line ${i}`, at }) as OrchestratorLogEntry),
        { kind: 'error', message: 'last', at },
      ];
      fake.log.set(entries);
      fixture.detectChanges();
      let saved: Blob | null = null;
      (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn((blob: Blob) => {
        saved = blob;
        return 'blob:log';
      });
      (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

      button('download-log').click();

      expect(click).toHaveBeenCalled();
      const text = await readBlob(saved as unknown as Blob);
      const lines = text.trimEnd().split('\n');
      expect(lines.length).toBe(602);
      expect(lines[0]).toBe('[09:08:07] first');
      expect(lines[601]).toBe('[09:08:07] ERROR: last');
    });

    it('disables copy and download until something has been logged', () => {
      expect(button('download-log').disabled).toBe(true);
      expect(button('copy-log').disabled).toBe(true);

      fake.log.set([{ kind: 'log', text: 'hi' }]);
      fixture.detectChanges();
      expect(button('download-log').disabled).toBe(false);
    });

    it('copies the log to the clipboard and says so', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
      fake.log.set([{ kind: 'log', text: 'one', at }, { kind: 'log', text: 'two', at }]);
      fixture.detectChanges();

      button('copy-log').click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(writeText).toHaveBeenCalledWith('[09:08:07] one\n[09:08:07] two\n');
      expect(fixture.nativeElement.querySelector('[data-testid="log-notice"]').textContent).toContain('Copied 2 lines');
    });

    it('keeps the newest line in view while following', async () => {
      fake.log.set([{ kind: 'log', text: 'a' }]);
      fixture.detectChanges();
      const el = pane();
      Object.defineProperty(el, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(el, 'scrollTop', { value: 0, writable: true, configurable: true });

      fake.log.set([...fake.log(), { kind: 'log', text: 'b' }]);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(el.scrollTop).toBe(1000);
    });

    it('stops following when the operator scrolls up, counts new lines, and jumps back', () => {
      fake.log.set([{ kind: 'log', text: 'a' }]);
      fixture.detectChanges();
      const el = pane();
      Object.defineProperty(el, 'scrollHeight', { value: 1000, configurable: true });
      Object.defineProperty(el, 'clientHeight', { value: 100, configurable: true });
      Object.defineProperty(el, 'scrollTop', { value: 0, writable: true, configurable: true });

      el.dispatchEvent(new Event('scroll')); // far from the bottom
      fake.log.set([...fake.log(), { kind: 'log', text: 'b' }, { kind: 'log', text: 'c' }]);
      fixture.detectChanges();

      expect(el.scrollTop).toBe(0); // not yanked back down while reading
      expect(button('jump-to-latest').textContent).toContain('2 new lines');

      button('jump-to-latest').click();
      fixture.detectChanges();

      expect(el.scrollTop).toBe(1000);
      expect(fixture.nativeElement.querySelector('[data-testid="jump-to-latest"]')).toBeNull();
    });
  });

  describe('Review Console', () => {
    function reviewOut(overrides: Partial<WorkflowReviewOut> = {}): WorkflowReviewOut {
      return {
        run_id: 'run-1',
        sample_id: 'S1',
        agent1_verdict: 'wrong part',
        agent2_verdict: 'missing part',
        conflict: true,
        diagnosis: 'Laser height is ~0 - the body is absent.',
        confidence: 0.91,
        self_check_passed: true,
        contradiction_detected: false,
        ipc_citations: ['IPC-A-610 Section 8.3.1'],
        visual_evidence: 'bare pads',
        errors: [],
        ...overrides,
      };
    }

    function reviewCase(overrides: Partial<WorkflowReviewCaseOut> = {}): WorkflowReviewCaseOut {
      return {
        run_id: 'run-1',
        sample_id: 'S1',
        board_id: 'Board1',
        component_ref: 'C978',
        feature_type: 'Body',
        agent1_verdict: 'wrong part',
        agent1_confidence: 0.6,
        has_golden_image: true,
        has_defect_image: true,
        review: reviewOut(),
        decision: null,
        ...overrides,
      };
    }

    async function showRun(cases: WorkflowReviewCaseOut[]): Promise<void> {
      fake.getReviewCases.mockResolvedValue(cases);
      fake.result.set({ ...runResult([sample()]), run_id: 'run-1' });
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
    }

    function consoleButton(): HTMLButtonElement {
      return Array.from(fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>).find(
        (btn) => btn.textContent?.includes('Open Review Console'),
      ) as HTMLButtonElement;
    }

    function popup(): HTMLElement | null {
      return fixture.nativeElement.querySelector('[data-testid="review-console"]');
    }

    async function openConsole(): Promise<void> {
      consoleButton().click();
      await fixture.whenStable();
      fixture.detectChanges();
    }

    async function openCase(sampleId = 'S1'): Promise<void> {
      if (popup() === null) {
        await openConsole();
      }
      (fixture.nativeElement.querySelector(`[data-testid="case-${sampleId}"]`) as HTMLElement).click();
      await fixture.whenStable();
      fixture.detectChanges();
    }

    function text(): string {
      return (fixture.nativeElement as HTMLElement).textContent ?? '';
    }

    it('moves focus into the console when it opens and back to the button when it closes', async () => {
      await showRun([reviewCase()]);
      consoleButton().focus();
      await openConsole();

      expect(document.activeElement).toBe(popup());

      fixture.componentInstance.closeConsole();
      fixture.detectChanges();
      expect(document.activeElement).toBe(consoleButton());
    });

    it('does not close the console while a decision is being saved', async () => {
      await showRun([reviewCase()]);
      await openCase('S1');
      let release: () => void = () => undefined;
      fake.saveReviewDecision.mockImplementationOnce(
        () => new Promise<void>((resolve) => (release = resolve)),
      );

      const confirm = submit();
      confirm.click();
      fixture.detectChanges();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      fixture.detectChanges();
      expect(popup()).not.toBeNull();
      expect(confirm.textContent).toContain('Saving');

      release();
      await fixture.whenStable();
    });

    it('confirms a decision and opens the next pending case', async () => {
      await showRun([reviewCase(), reviewCase({ sample_id: 'S2', review: null })]);
      await openCase('S1');

      const next = fixture.nativeElement.querySelector('[data-testid="confirm-decision"]') as HTMLButtonElement;
      expect(next.textContent).toContain('Confirm & next pending');
      next.click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fake.saveReviewDecision).toHaveBeenCalledWith(expect.objectContaining({ sample_id: 'S1' }));
      expect(text()).toContain('Sample: S2');
    });

    it('keeps the newest answer when an older cases request finishes last', async () => {
      await showRun([reviewCase()]);
      await openConsole();

      let resolveSlow: (cases: WorkflowReviewCaseOut[]) => void = () => undefined;
      fake.getReviewCases.mockImplementationOnce(
        () => new Promise<WorkflowReviewCaseOut[]>((resolve) => (resolveSlow = resolve)),
      );
      fake.getReviewCases.mockResolvedValueOnce([reviewCase({ sample_id: 'S9', component_ref: 'CNEWEST' })]);

      const slow = fixture.componentInstance.refreshCases();
      await fixture.componentInstance.refreshCases();
      resolveSlow([reviewCase({ sample_id: 'SOLD', component_ref: 'CSTALE' })]);
      await slow;
      fixture.detectChanges();

      expect(text()).toContain('CNEWEST');
      expect(text()).not.toContain('CSTALE');
    });

    it('keeps the queue rows mounted and the dialog size fixed when another case is selected', async () => {
      await showRun([reviewCase(), reviewCase({ sample_id: 'S2', review: null })]);
      await openConsole();
      const q = (id: string) => fixture.nativeElement.querySelector(`[data-testid="case-${id}"]`) as HTMLElement;
      const s1 = q('S1');
      const s2 = q('S2');
      expect(popup()?.className).toContain('h-[85vh]');

      await openCase('S2');
      await openCase('S1');

      expect(q('S1')).toBe(s1);
      expect(q('S2')).toBe(s2);
    });

    it('moves the selection with the arrow keys', async () => {
      await showRun([reviewCase(), reviewCase({ sample_id: 'S2', review: null })]);
      await openCase('S1');
      const row = fixture.nativeElement.querySelector('[data-testid="case-S1"]') as HTMLElement;

      row.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      await fixture.whenStable();
      fixture.detectChanges();

      expect(
        (fixture.nativeElement.querySelector('[data-testid="case-S2"]') as HTMLElement).getAttribute('aria-selected'),
      ).toBe('true');
    });

    it('lets the keyboard open a case from the queue', async () => {
      await showRun([reviewCase(), reviewCase({ sample_id: 'S2', review: null })]);
      await openConsole();
      const row = fixture.nativeElement.querySelector('[data-testid="case-S2"]') as HTMLElement;
      expect(row.getAttribute('tabindex')).toBe('0');

      row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      await fixture.whenStable();
      fixture.detectChanges();

      expect(row.getAttribute('aria-selected')).toBe('true');
    });

    function radios(): HTMLInputElement[] {
      return Array.from(fixture.nativeElement.querySelectorAll('input[name="decisionSource"]'));
    }

    function submit(): HTMLButtonElement {
      return Array.from(fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>).find(
        (btn) => btn.textContent?.includes('Confirm Decision'),
      ) as HTMLButtonElement;
    }

    beforeEach(() => {
      Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true, writable: true });
    });

    it('opens an explanatory empty popup when there is no run, or the run has nothing to review', async () => {
      await openConsole();
      expect(text()).toContain('no run to review yet');
      expect(fake.getReviewCases).not.toHaveBeenCalled();

      fake.result.set(runResult([sample()])); // no run_id
      fixture.detectChanges();
      expect(text()).toContain('no run to review yet');

      await showRun([]);
      expect(popup()).not.toBeNull();
      expect(text()).toContain('No samples in this run need review');
    });

    it('shows why the queue could not be loaded inside the popup', async () => {
      fake.getReviewCases.mockRejectedValue(new Error('explainability review agent is disabled'));
      fake.result.set({ ...runResult([sample()]), run_id: 'run-1' });
      fixture.detectChanges();
      await fixture.whenStable();

      await openConsole();

      expect(text()).toContain('explainability review agent is disabled');
    });

    it('opens the popup from the button, with a pending badge, and closes it again', async () => {
      await showRun([reviewCase(), reviewCase({ sample_id: 'S2', review: null })]);
      expect(consoleButton().textContent).toContain('2');
      expect(popup()).toBeNull(); // not shown until asked for

      await openConsole();
      expect(popup()?.getAttribute('aria-modal')).toBe('true');
      expect(text()).toContain('Review Console');
      expect(text()).toContain('Run: run-1');

      (fixture.nativeElement.querySelector('button[aria-label="close review console"]') as HTMLElement).click();
      fixture.detectChanges();
      expect(popup()).toBeNull();

      await openConsole();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      fixture.detectChanges();
      expect(popup()).toBeNull();
    });

    it('closes when the backdrop is clicked but not when the dialog itself is', async () => {
      await showRun([reviewCase()]);
      await openConsole();

      popup()?.click();
      fixture.detectChanges();
      expect(popup()).not.toBeNull();

      (fixture.nativeElement.querySelector('[data-testid="review-console-backdrop"]') as HTMLElement).click();
      fixture.detectChanges();
      expect(popup()).toBeNull();
    });

    it('selects the first pending case when opened', async () => {
      await showRun([
        reviewCase({
          sample_id: 'S1',
          decision: {
            run_id: 'run-1',
            sample_id: 'S1',
            selected_source: 'MACHINE',
            final_result: 'wrong part',
            machine_result: 'wrong part',
            ai_result: null,
            operator_notes: null,
            decided_by_user_id: 'u1',
          },
        }),
        reviewCase({ sample_id: 'S2' }),
      ]);

      await openConsole();

      expect(fixture.nativeElement.querySelector('[data-testid="review-detail"]')?.textContent).toContain(
        'Sample: S2',
      );
    });

    it('filters the queue by pending and reviewed', async () => {
      await showRun([
        reviewCase({ sample_id: 'S1' }),
        reviewCase({
          sample_id: 'S2',
          decision: {
            run_id: 'run-1',
            sample_id: 'S2',
            selected_source: 'AI',
            final_result: 'missing part',
            machine_result: 'wrong part',
            ai_result: 'missing part',
            operator_notes: null,
            decided_by_user_id: 'u1',
          },
        }),
      ]);
      await openConsole();
      const select = fixture.nativeElement.querySelector('select[name="caseFilter"]') as HTMLSelectElement;
      const rows = (): string[] =>
        Array.from(fixture.nativeElement.querySelectorAll('[data-testid^="case-"]')).map(
          (row) => (row as HTMLElement).getAttribute('data-testid') ?? '',
        );
      expect(rows()).toEqual(['case-S1', 'case-S2']);

      select.value = 'pending';
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
      expect(rows()).toEqual(['case-S1']);

      select.value = 'reviewed';
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
      expect(rows()).toEqual(['case-S2']);
    });

    it('lists every case with the machine verdict, Agent 2 verdict and status', async () => {
      await showRun([
        reviewCase(),
        reviewCase({ sample_id: 'S2', review: null }),
        reviewCase({
          sample_id: 'S3',
          decision: {
            run_id: 'run-1',
            sample_id: 'S3',
            selected_source: 'AI',
            final_result: 'missing part',
            machine_result: 'wrong part',
            ai_result: 'missing part',
            operator_notes: null,
            decided_by_user_id: 'u1',
          },
        }),
      ]);

      expect(fake.getReviewCases).toHaveBeenCalledWith('run-1');
      await openConsole();
      expect(text()).toContain('Items Needing Explanation (1 of 3 reviewed)');
      expect(text()).toContain('Unknown'); // S2: Agent 2 has not finished
      expect(text()).toContain('Reviewed');
      expect(text()).toContain('Pending');
    });

    it('refetches the cases each time Agent 2 finishes a sample', async () => {
      await showRun([reviewCase({ review: null })]);
      await openConsole();
      expect(text()).toContain('Unknown');
      const callsBefore = fake.getReviewCases.mock.calls.length;

      fake.getReviewCases.mockResolvedValue([reviewCase()]);
      fake.reviewTick.set(1);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fake.getReviewCases.mock.calls.length).toBeGreaterThan(callsBefore);
      expect(text()).toContain('missing part');
      expect(text()).not.toContain('Unknown');
    });

    it('opens a case with its images and Agent 2 explanation', async () => {
      await showRun([reviewCase()]);

      await openCase();

      expect(fake.getReviewImageUrl).toHaveBeenCalledWith('run-1', 'S1', 'golden');
      expect(fake.getReviewImageUrl).toHaveBeenCalledWith('run-1', 'S1', 'defect');
      expect(fixture.nativeElement.querySelector('img[alt="golden reference"]')?.getAttribute('src')).toBe(
        'blob:run-1-S1-golden',
      );
      expect(fixture.nativeElement.querySelector('img[alt="defect crop"]')).not.toBeNull();
      expect(text()).toContain('Laser height is ~0');
      expect(text()).toContain('IPC-A-610 Section 8.3.1');
    });

    it('does not request images the server does not have', async () => {
      await showRun([reviewCase({ has_golden_image: false })]);

      await openCase();

      expect(fake.getReviewImageUrl).not.toHaveBeenCalledWith('run-1', 'S1', 'golden');
      expect(fixture.nativeElement.querySelector('img[alt="golden reference"]')).toBeNull();
      expect(text()).toContain('No image available');
    });

    it('defaults to the machine verdict and saves it with the notes', async () => {
      await showRun([reviewCase()]);
      await openCase();
      expect(radios()[0].checked).toBe(true);
      const notes = fixture.nativeElement.querySelector('input[name="operatorNotes"]') as HTMLInputElement;
      notes.value = ' looks fine ';
      notes.dispatchEvent(new Event('input'));

      submit().click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fake.saveReviewDecision).toHaveBeenCalledWith({
        run_id: 'run-1',
        sample_id: 'S1',
        selected_source: 'MACHINE',
        final_result: 'wrong part',
        machine_result: 'wrong part',
        ai_result: 'missing part',
        ai_diagnosis: 'Laser height is ~0 - the body is absent.',
        operator_notes: 'looks fine',
      });
      expect(text()).toContain('Saved S1: wrong part');
    });

    it('saves Agent 2 or a manual IPC class when picked', async () => {
      await showRun([reviewCase()]);
      await openCase();

      radios()[1].click(); // AI
      submit().click();
      await fixture.whenStable();
      expect(fake.saveReviewDecision).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_source: 'AI', final_result: 'missing part' }),
      );

      const select = fixture.nativeElement.querySelector('select[name="manualClass"]') as HTMLSelectElement;
      select.value = 'tombstone';
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
      submit().click();
      await fixture.whenStable();
      expect(fake.saveReviewDecision).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_source: 'MANUAL', final_result: 'tombstone' }),
      );
    });

    it('cannot pick Agent 2 while its review is still pending', async () => {
      await showRun([reviewCase({ review: null })]);
      await openCase();

      expect(radios()[1].disabled).toBe(true);
      expect(text()).toContain('Agent 2 review is still pending');
    });

    it('pre-fills the form from a saved decision', async () => {
      await showRun([
        reviewCase({
          decision: {
            run_id: 'run-1',
            sample_id: 'S1',
            selected_source: 'MANUAL',
            final_result: 'tombstone',
            machine_result: 'wrong part',
            ai_result: 'missing part',
            operator_notes: 'seen under scope',
            decided_by_user_id: 'u1',
          },
        }),
      ]);

      await openCase();

      expect(radios()[2].checked).toBe(true);
      expect((fixture.nativeElement.querySelector('select[name="manualClass"]') as HTMLSelectElement).value).toBe(
        'tombstone',
      );
      expect((fixture.nativeElement.querySelector('input[name="operatorNotes"]') as HTMLInputElement).value).toBe(
        'seen under scope',
      );
    });

    it('shows the server error when saving fails', async () => {
      fake.saveReviewDecision.mockRejectedValueOnce(new Error('selected_source must be MACHINE, AI, or MANUAL'));
      await showRun([reviewCase()]);
      await openCase();

      submit().click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(text()).toContain('selected_source must be');
    });

    it('clearing the log empties the console', async () => {
      await showRun([reviewCase()]);
      await openCase();

      fixture.componentInstance.clearLog();
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(popup()).toBeNull();
    });

    describe('Corrections & Retraining: how often the model was wrong, and sending corrections for retraining', () => {
      async function openDriftTab(): Promise<void> {
        await openConsole();
        (fixture.nativeElement.querySelector('[data-testid="tab-drift"]') as HTMLButtonElement).click();
        await fixture.whenStable();
        fixture.detectChanges();
      }

      const tidy = (el: Element | null | undefined): string => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
      const byTestId = (id: string): Element | null => fixture.nativeElement.querySelector(`[data-testid="${id}"]`);
      const allByTestId = (id: string): string[] =>
        Array.from(fixture.nativeElement.querySelectorAll(`[data-testid="${id}"]`) as NodeListOf<Element>).map(tidy);

      function sendButton(): HTMLButtonElement {
        return byTestId('send-for-retraining') as HTMLButtonElement;
      }

      /** The per-sample checkboxes (not the select-all in the header). */
      function boxes(): HTMLInputElement[] {
        return Array.from(fixture.nativeElement.querySelectorAll('[data-testid="corrections"] tbody input[type="checkbox"]'));
      }

      function selectAll(): HTMLInputElement {
        return byTestId('select-all') as HTMLInputElement;
      }

      async function settle(): Promise<void> {
        await fixture.whenStable();
        fixture.detectChanges();
      }

      it('headlines the defect model\'s error rate, and what that is made of', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ sample_id: 'S2' }), correction({ sample_id: 'S3', outcome: 'no_prediction', model_name: null, agent1_label: null, queueable: false })], {
            totals: { samples: 6, review_required: 6, decided: 4, confirmed: 2, relabelled: 1, no_prediction: 1, corrected: 1 },
            drift_rate: 0.333,
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(fake.getRunDrift).toHaveBeenCalledWith('run-1');
        expect(tidy(byTestId('drift-rate'))).toBe('33%');
        expect(tidy(byTestId('drift-of'))).toBe('1 of 3 the defect model classified were wrong'); // couldn't-decide not counted
        expect(tidy(byTestId('review-progress'))).toBe('4 of 6 flagged samples reviewed');
        expect(tidy(byTestId('kpi-confirmed'))).toBe('2');
        expect(tidy(byTestId('kpi-relabelled'))).toBe('1');
        expect(tidy(byTestId('kpi-no-answer'))).toBe('1');
        const row = fixture.nativeElement.querySelector('[data-model="pcb_body_defect"]') as HTMLElement;
        expect(tidy(row.querySelector('[data-testid="corrected"]'))).toBe('50%');
        expect(tidy(row)).toContain('2 of 2 reviewed'); // corrected of reviewed, per model
        // a sample the model couldn't decide is explained, not left as a bare number
        expect(tidy(byTestId('no-model-note'))).toContain('do not count toward the error rate');
      });

      it('says the rate is partial while review is unfinished, and shows nothing reviewed as a dash, not 0%', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(tidy(byTestId('drift-rate'))).toBe('—');
        expect(tidy(byTestId('drift-of'))).toBe('nothing reviewed yet');
        expect(tidy(byTestId('review-progress'))).toBe('0 of 2 flagged samples reviewed - this changes as you review');
        expect(tidy(byTestId('activity-empty'))).toContain('No decisions yet');
        expect(tidy(byTestId('no-corrections'))).toBe('Nothing reviewed yet.');
        expect(sendButton().disabled).toBe(true);
      });

      it('shows what is still unreviewed in the outcome bar, and the model\'s commonest mistakes', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })], {
            decisions: {
              by_source: {
                MACHINE: { chosen: 3, overruled_model: 1 },
                AI: { chosen: 1, overruled_model: 1 },
                MANUAL: { chosen: 0, overruled_model: 0 },
              },
              pending: 7,
              top_overruled: [{ model_label: 'missing part', chosen_label: 'wrong part', count: 2 }],
            },
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(tidy(byTestId('kpi-pending'))).toBe('7');
        expect(allByTestId('top-overruled')[0]).toContain('model said missing part → correct label wrong part');
        // how the operator's own choices split says nothing about the model, so it is not charted
        expect(fixture.nativeElement.textContent).not.toContain('Your final decisions');
        expect(fixture.nativeElement.querySelector('[data-activity]')).toBeNull();
      });

      it('says where the corrections stand and tracks them through to a retrained model', async () => {
        const stage = (key: string): string => tidy(fixture.nativeElement.querySelector(`[data-stage="${key}"]`));
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(byTestId('feedback-status')?.getAttribute('data-kind')).toBe('send');
        expect(tidy(byTestId('feedback-status'))).toContain('You corrected 2 of 2 reviewed samples. 2 are ready to send.');
        expect(stage('to-send')).toContain('2');

        fake.getRunDrift.mockResolvedValue(
          runDrift([
            correction({ sample_id: 'S1', queued: true, ticket_status: 'acknowledged' }),
            correction({ sample_id: 'S2', queued: true, ticket_status: 'resolved' }),
          ]),
        );
        await fixture.componentInstance.refreshDrift();
        fixture.detectChanges();

        expect(tidy(byTestId('feedback-status'))).toContain('All 2 corrections have been sent.');
        expect(stage('approval')).toContain('1');
        expect(stage('retrained')).toContain('1');
        expect(stage('to-send')).toContain('0');
      });

      it('charts how the model did: headline numbers, outcome donut, per-model and per-decision bars', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ sample_id: 'S1' })], {
            totals: { samples: 10, review_required: 10, decided: 8, confirmed: 5, relabelled: 2, no_prediction: 1, corrected: 3 },
            drift_rate: 2 / 7,
            decisions: {
              by_source: {
                MACHINE: { chosen: 5, overruled_model: 0 },
                AI: { chosen: 1, overruled_model: 1 },
                MANUAL: { chosen: 2, overruled_model: 2 },
              },
              pending: 2,
              top_overruled: [{ model_label: 'missing part', chosen_label: 'wrong part', count: 2 }],
            },
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        const kpis = tidy(byTestId('drift-summary'));
        expect(kpis).toContain('Reviewed80%');
        expect(kpis).toContain('Model accuracy71%'); // 5 right of the 7 the model classified
        expect(kpis).toContain('Corrections3');
        expect(fixture.nativeElement.querySelectorAll('[data-testid="outcome-bar"] svg circle').length).toBe(5); // track + 4 arcs
        expect(fixture.nativeElement.querySelector('[data-model="pcb_body_defect"]')).not.toBeNull();
        expect(tidy(byTestId('review-activity'))).toContain('Accept Machine');
        expect(tidy(byTestId('review-activity'))).toContain('2 chosen, 2 overruled the model');
      });

      it('asks for the rest of the review first, with a way back', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([], {
            decisions: { by_source: {}, pending: 3, top_overruled: [] },
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(tidy(byTestId('feedback-status'))).toContain('3 flagged samples still need review');
        (byTestId('go-review') as HTMLButtonElement).click();
        fixture.detectChanges();
        expect(popup()?.textContent).toContain('Items Needing Explanation');
      });

      it('lists each correction in the review tab\'s own words, with everything ticked ready to send', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(allByTestId('said-model')).toEqual(['missing part', 'missing part']);
        expect(allByTestId('chose')).toEqual(['tombstone (Manual)', 'tombstone (Manual)']);
        expect(allByTestId('correction-status')).toEqual(['Not sent yet', 'Not sent yet']);
        expect(boxes().every((box) => box.checked && !box.disabled)).toBe(true);
        expect(sendButton().disabled).toBe(false);
        expect(tidy(sendButton())).toBe('Send 2 corrections for retraining');
        expect(tidy(byTestId('corrections-badge'))).toBe('2 to send');
      });

      it('selects every sendable correction at once', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(boxes().every((box) => box.checked)).toBe(true);
        expect(tidy(sendButton())).toBe('Send 2 corrections for retraining');

        selectAll().click();
        fixture.detectChanges();
        expect(boxes().every((box) => !box.checked)).toBe(true);
        expect(sendButton().disabled).toBe(true);

        selectAll().click();
        fixture.detectChanges();
        expect(boxes().every((box) => box.checked)).toBe(true);
      });

      it('refetches after the operator saves a final decision', async () => {
        await showRun([reviewCase()]);
        await openCase();
        fake.getRunDrift.mockClear();
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));

        submit().click();
        await settle();

        expect(fake.getRunDrift).toHaveBeenCalledWith('run-1');
        expect(tidy(byTestId('corrections-badge'))).toBe('1 to send');
      });

      it('sends the ticked corrections and puts them in a retraining plan', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        fake.queueCorrections.mockResolvedValue({
          created: [{ id: 't2', sample_ref: 'S2', model_name: 'pcb_body_defect', status: 'open' }],
          already_queued: [],
        });
        await showRun([reviewCase()]);
        await openDriftTab();
        boxes()[0].click(); // untick S1, leaving S2
        fixture.detectChanges();
        expect(tidy(sendButton())).toBe('Send 1 correction for retraining');

        fake.getRunDrift.mockResolvedValue(
          runDrift([
            correction({ sample_id: 'S1' }),
            correction({ sample_id: 'S2', queued: true, ticket_status: 'acknowledged' }),
          ]),
        );
        sendButton().click();
        await settle();

        // only ids go to the server - it builds the training sample from what it stored
        expect(fake.queueCorrections).toHaveBeenCalledWith({ run_id: 'run-1', sample_ids: ['S2'] });
        expect(fake.draftRetrainingPlan).toHaveBeenCalledWith('pcb_body_defect');
        expect(tidy(byTestId('action-message'))).toBe(
          'Sent 1 correction for retraining. The retraining plan for pcb_body_defect is waiting for an Admin to approve it in the Models tab.',
        );
        expect(allByTestId('correction-status')).toEqual([
          'Not sent yet',
          'In a retraining plan, awaiting Admin approval',
        ]);
        // a sent sample shows a tick, not a greyed-out checkbox
        expect(boxes().length).toBe(1);
        expect(fixture.nativeElement.querySelector('[data-sample="S2"] [aria-label="already sent"]')).not.toBeNull();
      });

      it('cannot send a sample with no model to route to, or one already sent', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([
            correction({ sample_id: 'S1', queued: true, ticket_status: 'open' }),
            correction({
              sample_id: 'S2',
              outcome: 'no_prediction',
              model_name: null,
              model_source: null,
              region_guess: { label: 'Gap', confidence: 0.4 },
              agent1_label: null,
              queueable: false,
            }),
          ]),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(boxes().length).toBe(1); // S1 shows a tick instead
        expect(boxes()[0].disabled && !boxes()[0].checked).toBe(true);
        expect(selectAll().disabled).toBe(true);
        expect(sendButton().disabled).toBe(true);
        expect(allByTestId('said-model')[1]).toBe("unsure (first guess: Gap area 40%)");
        expect(allByTestId('correction-status')).toEqual([
          'Sent, waiting for a retraining plan',
          "Can't send: no defect model to route it to",
        ]);
        expect(byTestId('corrections-badge')).toBeNull();
      });

      it('a run where the model couldn\'t decide anything has no error rate, and the labels can still be sent', async () => {
        const undecided = (id: string, label: string): WorkflowCorrection =>
          correction({
            sample_id: id,
            outcome: 'no_prediction',
            agent1_label: null,
            model_name: 'pcb_text_defect',
            model_version: null,
            model_source: 'region_guess',
            region_guess: { label: 'Text', confidence: 0.5 },
            final_result: label,
            selected_source: 'MACHINE',
          });
        fake.getRunDrift.mockResolvedValue(
          runDrift([undecided('S1', 'solder insufficient'), undecided('S2', 'polarity1')], {
            totals: { samples: 11, review_required: 11, decided: 2, confirmed: 0, relabelled: 0, no_prediction: 2, corrected: 0 },
            drift_rate: null,
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        // not "100% wrong": the model never answered
        expect(tidy(byTestId('drift-rate'))).toBe('—');
        expect(tidy(byTestId('drift-of'))).toBe('the defect model classified none of the reviewed samples');
        expect(tidy(byTestId('kpi-no-answer'))).toBe('2');
        expect(allByTestId('said-model')).toEqual([
          "unsure (first guess: Text area 50%)",
          "unsure (first guess: Text area 50%)",
        ]);
        expect(allByTestId('target-model')).toEqual(['pcb_text_defect (from first guess)', 'pcb_text_defect (from first guess)']);
        expect(allByTestId('chose')).toEqual(['solder insufficient (Accept Machine)', 'polarity1 (Accept Machine)']);
        expect(tidy(sendButton())).toBe('Send 2 corrections for retraining');
      });

      it('shows the server error when sending is rejected, keeping the selection', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        fake.queueCorrections.mockRejectedValue(new Error('nothing to add for sample(s) S1'));
        await showRun([reviewCase()]);
        await openDriftTab();

        sendButton().click();
        await settle();

        expect(tidy(byTestId('action-error'))).toContain('nothing to add for sample(s) S1');
        expect(fake.draftRetrainingPlan).not.toHaveBeenCalled();
        expect(sendButton().disabled).toBe(false);
      });

      it('offers to create the plan when sent samples are not in one, and says what else it would take', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        fake.queueCorrections.mockResolvedValue({
          created: [{ id: 't1', sample_ref: 'S1', model_name: 'pcb_body_defect', status: 'open' }],
          already_queued: [],
        });
        fake.draftRetrainingPlan.mockRejectedValueOnce(new Error('cannot tell which version'));
        await showRun([reviewCase()]);
        await openDriftTab();

        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ queued: true, ticket_status: 'open' })], {
            open_tickets: { pcb_body_defect: 3 },
            run_open_tickets: { pcb_body_defect: 1 },
          }),
        );
        sendButton().click();
        await settle();

        expect(tidy(byTestId('action-message'))).toContain('No plan could be created for pcb_body_defect yet');
        const leftover = fixture.nativeElement.querySelector('[data-plan-model="pcb_body_defect"]') as HTMLElement;
        expect(tidy(leftover)).toContain('1 sample from this run sent but not in a retraining plan yet');
        expect(tidy(leftover)).toContain('the plan also takes 2 more waiting from chat or other runs');

        fake.getRunDrift.mockResolvedValue(runDrift([correction({ queued: true, ticket_status: 'acknowledged' })]));
        (byTestId('draft-plan') as HTMLButtonElement).click();
        await settle();

        expect(fake.draftRetrainingPlan).toHaveBeenLastCalledWith('pcb_body_defect');
        expect(tidy(byTestId('action-message'))).toContain('Plan drafted for pcb_body_defect');
        expect(byTestId('action-error')).toBeNull();
        expect(fixture.nativeElement.querySelector('[data-plan-model]')).toBeNull();
      });

      it('does not offer a plan for tickets that belong to other runs', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ queued: true, ticket_status: 'acknowledged' })], {
            open_tickets: { pcb_lead_defect: 4 },
            run_open_tickets: {},
          }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(byTestId('retraining-plan')).toBeNull();
      });

      it('has no manual tools any more', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        await showRun([reviewCase()]);
        await openDriftTab();

        const text = fixture.nativeElement.textContent as string;
        expect(byTestId('manual-tools')).toBeNull();
        expect(text).not.toContain('Report drift');
        expect(text).not.toContain('Flag for retraining');
      });

      it('says what to do first when there is no run yet, instead of showing a blank tab', async () => {
        await openDriftTab();

        expect(tidy(byTestId('drift-no-run'))).toContain('Run the agentic workflow');
        expect(fixture.nativeElement.querySelector('#review-console-title').textContent).toContain(
          'Model feedback',
        );
      });

      it('says so when the stored run cannot be read, and when loading fails', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([], { available: false, message: 'The stored run could not be read right now', models: [] }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();
        expect(tidy(byTestId('drift-unavailable'))).toContain('could not be read');

        fake.getRunDrift.mockRejectedValue(new Error('boom'));
        await fixture.componentInstance.refreshDrift();
        fixture.detectChanges();
        expect(tidy(byTestId('drift-error'))).toContain('boom');
      });
    });
  });
});
