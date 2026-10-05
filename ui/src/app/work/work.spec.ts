import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { vi } from 'vitest';
import { Work } from './work';
import { WorkService } from './work.service';
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
    final_result: 'tombstone',
    selected_source: 'MANUAL',
    operator_notes: 'bare pads',
    queueable: true,
    queued: false,
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
    totals: { samples: 2, review_required: 2, decided: corrections.length, corrected: corrections.length },
    models: [
      {
        model_name: 'pcb_body_defect',
        model_version: 'JcProg/body@v2',
        samples: 2,
        review_required: 2,
        decided: corrections.length,
        corrected: corrections.length,
        correction_rate: corrections.length > 0 ? 0.5 : null,
        agent2_disagreed: 1,
        low_confidence_rate: 0.5,
        mean_confidence: 0.7,
      },
    ],
    corrections,
    ...overrides,
  };
}

function fakeWorkService(): {
  service: WorkService;
  log: ReturnType<typeof signal<OrchestratorLogEntry[]>>;
  result: ReturnType<typeof signal<OrchestratorRunResult | null>>;
  run: ReturnType<typeof vi.fn>;
  clearLog: ReturnType<typeof vi.fn>;
  reportDrift: ReturnType<typeof vi.fn>;
  flagForRetraining: ReturnType<typeof vi.fn>;
  getReviewCases: ReturnType<typeof vi.fn>;
  getReviewImageUrl: ReturnType<typeof vi.fn>;
  saveReviewDecision: ReturnType<typeof vi.fn>;
  getRunDrift: ReturnType<typeof vi.fn>;
  queueCorrections: ReturnType<typeof vi.fn>;
  reviewTick: ReturnType<typeof signal<number>>;
} {
  const log = signal<OrchestratorLogEntry[]>([]);
  const result = signal<OrchestratorRunResult | null>(null);
  const run = vi.fn();
  const clearLog = vi.fn(() => {
    log.set([]);
    result.set(null);
  });
  const reportDrift = vi.fn().mockResolvedValue({
    id: 'r1',
    model_name: 'pcb_body_defect',
    model_version: null,
    status: 'open',
  });
  const flagForRetraining = vi
    .fn()
    .mockResolvedValue([{ id: 't1', sample_ref: 'S1', model_name: 'pcb_body_defect', status: 'open' }]);

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

  const service = {
    status: signal(IDLE_STATUS),
    log,
    running: signal(false),
    result,
    getStatus: vi.fn().mockResolvedValue({ llm_configured: false }),
    uploadDataset: vi.fn().mockResolvedValue('dataset-1'),
    uploadXml: vi.fn().mockResolvedValue('xml-1'),
    uploadImageRootFiles: vi.fn().mockResolvedValue('root-1'),
    run,
    clearLog,
    reportDrift,
    flagForRetraining,
    reviewTick,
    getReviewCases,
    getReviewImageUrl,
    saveReviewDecision,
    getRunDrift,
    queueCorrections,
  } as unknown as WorkService;

  return {
    service,
    log,
    result,
    run,
    clearLog,
    reportDrift,
    flagForRetraining,
    getReviewCases,
    getReviewImageUrl,
    saveReviewDecision,
    getRunDrift,
    queueCorrections,
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
    fake = fakeWorkService();
    await TestBed.configureTestingModule({
      imports: [Work],
      providers: [{ provide: WorkService, useValue: fake.service }],
    }).compileComponents();

    fixture = TestBed.createComponent(Work);
    fixture.detectChanges();
  });

  function runButton(): HTMLButtonElement {
    return Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLButtonElement).textContent?.includes('Run Agentic Workflow'),
    ) as HTMLButtonElement;
  }

  it('shows the OPENAI_API_KEY status label once the status call resolves', async () => {
    expect(fixture.nativeElement.textContent).not.toContain('OPENAI_API_KEY');

    await fixture.whenStable();
    fixture.detectChanges();

    // fakeWorkService()'s default getStatus() resolves { llm_configured: false }.
    expect(fixture.nativeElement.textContent).toContain('OPENAI_API_KEY not detected');
  });

  it('shows nothing when the status call fails, rather than a stale/wrong label', async () => {
    fake = fakeWorkService();
    (fake.service.getStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('offline'));
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Work],
      providers: [{ provide: WorkService, useValue: fake.service }],
    }).compileComponents();
    fixture = TestBed.createComponent(Work);
    fixture.detectChanges();

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

  it('defaults the output filename to result.json and uses it for the download label/name', () => {
    fake.result.set(runResult([]));
    fixture.detectChanges();

    const downloadButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLButtonElement).textContent?.includes('Download'),
    ) as HTMLButtonElement;

    expect(downloadButton.textContent).toContain('Download result.json');
  });

  describe('per-sample results, drift reports and retraining tickets', () => {
    // Drift reporting and retraining flags live in the Review Console's "Drift & Retraining" tab.
    function showDriftTab(): void {
      const buttons = Array.from(fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>);
      buttons.find((btn) => btn.textContent?.includes('Open Review Console'))?.click();
      fixture.detectChanges();
      (fixture.nativeElement.querySelector('[data-testid="tab-drift"]') as HTMLButtonElement).click();
      fixture.detectChanges();
    }

    function checkboxes(): HTMLInputElement[] {
      // Scoped to the results section - the form above it also has checkboxes ("Use real LLM
      // Planner", "Fallback to deterministic planner").
      return Array.from(
        fixture.nativeElement.querySelectorAll(
          '[data-testid="results-section"] input[type="checkbox"]',
        ),
      );
    }

    function findButton(label: string): HTMLButtonElement {
      return Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
        (btn as HTMLButtonElement).textContent?.trim().startsWith(label),
      ) as HTMLButtonElement;
    }

    // The drift/retraining form fields are protected component state, driven through ngModel -
    // interact via the DOM like the rest of this file does (e.g. onDatasetFileChange), not by
    // poking the signals directly.
    function setSelect(name: string, value: string): void {
      const select = fixture.nativeElement.querySelector(`select[name="${name}"]`) as HTMLSelectElement;
      select.value = value;
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    }

    function setTextarea(name: string, value: string): void {
      const textarea = fixture.nativeElement.querySelector(
        `textarea[name="${name}"]`,
      ) as HTMLTextAreaElement;
      textarea.value = value;
      textarea.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    }

    function checkedCount(): number {
      return checkboxes().filter((cb) => cb.checked).length;
    }

    it('shows no results panel before a run has produced any samples', () => {
      expect(checkboxes()).toHaveLength(0);
    });

    it('renders one row per sample with the real model name, not the routing key', () => {
      fake.result.set(runResult([sample({ sample_id: 'S1' }), sample({ sample_id: 'S2' })]));
      showDriftTab();

      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(checkboxes()).toHaveLength(2);
      expect(text).toContain('S1');
      expect(text).toContain('S2');
      expect(text).toContain('pcb_body_defect');
      expect(text).not.toContain('>body<'); // the raw routing key never leaks into the row
    });

    it('keeps the drift tools inside the Review Console tab, not on the page', () => {
      fake.result.set(runResult([sample()]));
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('[data-testid="results-section"]')).toBeNull();

      showDriftTab();
      expect(fixture.nativeElement.querySelector('[data-testid="results-section"]')).not.toBeNull();

      (fixture.nativeElement.querySelector('[data-testid="tab-review"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('[data-testid="results-section"]')).toBeNull();
    });

    it('a sample that never reached routing shows placeholders, not an error', () => {
      fake.result.set(runResult([sample({ routing: undefined, defect_classification: undefined })]));
      showDriftTab();

      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('—');
    });

    it('the model picker only offers models actually seen in the results', () => {
      fake.result.set(
        runResult([
          sample({ sample_id: 'S1', routing: { selected_model: 'body', service_model: 'pcb_body_defect' } }),
          sample({ sample_id: 'S2', routing: { selected_model: 'lead', service_model: 'pcb_lead_defect' } }),
          sample({ sample_id: 'S3', routing: undefined }),
        ]),
      );
      showDriftTab();

      const options = Array.from(fixture.nativeElement.querySelectorAll('option'))
        .map((o) => (o as HTMLOptionElement).value)
        .filter((v) => v !== '');
      expect(options).toEqual(['pcb_body_defect', 'pcb_lead_defect']);
    });

    it('reports drift for the chosen model with every sample as evidence, once a model and description are given', async () => {
      fake.result.set(runResult([sample()]));
      showDriftTab();

      expect(findButton('Report drift').disabled).toBe(true);

      setSelect('driftModelName', 'pcb_body_defect');
      setTextarea('driftDescription', 'elevated review rate');
      expect(findButton('Report drift').disabled).toBe(false);

      await fixture.componentInstance.reportDrift();

      expect(fake.reportDrift).toHaveBeenCalledWith({
        model_name: 'pcb_body_defect',
        description: 'elevated review rate',
        samples: [sample()],
      });
    });

    it('flag-for-retraining is disabled until a sample is selected and a reason is given', () => {
      fake.result.set(runResult([sample()]));
      showDriftTab();

      expect(findButton('Flag for retraining').disabled).toBe(true);

      checkboxes()[0].click();
      fixture.detectChanges();
      expect(findButton('Flag for retraining').disabled).toBe(true); // still no reason

      setTextarea('retrainingReason', 'false positive');
      expect(findButton('Flag for retraining').disabled).toBe(false);
    });

    it('flags only the selected samples, then clears the selection on success', async () => {
      fake.result.set(runResult([sample({ sample_id: 'S1' }), sample({ sample_id: 'S2' })]));
      showDriftTab();
      checkboxes()[1].click(); // S2
      setTextarea('retrainingReason', 'false positive');

      await fixture.componentInstance.flagSelectedForRetraining();
      fixture.detectChanges();

      expect(fake.flagForRetraining).toHaveBeenCalledWith({
        tickets: [{ sample: sample({ sample_id: 'S2' }), reason: 'false positive' }],
      });
      expect(checkedCount()).toBe(0);
    });

    it('shows the server error when flagging is rejected, without clearing the selection', async () => {
      fake.flagForRetraining.mockRejectedValue(new Error('no resolvable model for sample(s): S1'));
      fake.result.set(runResult([sample()]));
      showDriftTab();
      checkboxes()[0].click();
      setTextarea('retrainingReason', 'x');

      await fixture.componentInstance.flagSelectedForRetraining();
      fixture.detectChanges();

      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('no resolvable model for sample(s): S1');
      expect(checkedCount()).toBe(1);
    });

    it('clearing the log also clears the sample selection', () => {
      fake.result.set(runResult([sample()]));
      showDriftTab();
      checkboxes()[0].click();
      fixture.detectChanges();
      expect(checkedCount()).toBe(1);

      fixture.componentInstance.clearLog();
      fixture.detectChanges();

      expect(checkedCount()).toBe(0);
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
      expect(text()).toContain('Explanation Review Queue');
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

    describe('Drift & Retraining follows the Explanation Review', () => {
      function driftText(): string {
        return (fixture.nativeElement.querySelector('[data-testid="run-drift"]') as HTMLElement | null)?.textContent ?? '';
      }

      async function openDriftTab(): Promise<void> {
        await openConsole();
        (fixture.nativeElement.querySelector('[data-testid="tab-drift"]') as HTMLButtonElement).click();
        await fixture.whenStable();
        fixture.detectChanges();
      }

      function queueButton(): HTMLButtonElement {
        return fixture.nativeElement.querySelector('[data-testid="queue-corrections"]') as HTMLButtonElement;
      }

      function correctionBoxes(): HTMLInputElement[] {
        return Array.from(
          fixture.nativeElement.querySelectorAll('[data-testid="corrections"] input[type="checkbox"]'),
        );
      }

      it('shows the server\'s per-model numbers for the run', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(fake.getRunDrift).toHaveBeenCalledWith('run-1');
        const row = fixture.nativeElement.querySelector('[data-model="pcb_body_defect"]') as HTMLElement;
        expect(row.textContent).toContain('pcb_body_defect');
        expect(row.querySelector('[data-testid="corrected"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
          '1 (50%)',
        );
        expect(row.textContent).toContain('70%'); // mean confidence
      });

      it('lists the operator\'s corrections, pre-selected, and says when there are none', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([]));
        await showRun([reviewCase()]);
        await openDriftTab();
        expect(fixture.nativeElement.querySelector('[data-testid="no-corrections"]')).not.toBeNull();
        expect(queueButton().disabled).toBe(true);

        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        await fixture.componentInstance.refreshDrift();
        fixture.detectChanges();

        expect(driftText()).toContain('MissingPart → tombstone');
        expect(correctionBoxes()).toHaveLength(2);
        expect(correctionBoxes().every((box) => box.checked)).toBe(true);
        expect(queueButton().textContent).toContain('Queue 2 for retraining');
        expect(queueButton().disabled).toBe(false);
      });

      it('refetches the drift numbers after the operator saves a decision', async () => {
        await showRun([reviewCase()]);
        await openCase();
        fake.getRunDrift.mockClear();
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));

        submit().click();
        await fixture.whenStable();
        fixture.detectChanges();

        expect(fake.getRunDrift).toHaveBeenCalledWith('run-1');
        expect(fixture.nativeElement.querySelector('[data-testid="corrections-badge"]')?.textContent).toContain(
          '1 to queue',
        );
      });

      it('queues only the ticked corrections and sends ids, not sample data', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]),
        );
        fake.queueCorrections.mockResolvedValue({
          created: [{ id: 't1', sample_ref: 'S2', model_name: 'pcb_body_defect', status: 'open' }],
          already_queued: [],
        });
        await showRun([reviewCase()]);
        await openDriftTab();
        correctionBoxes()[0].click(); // untick S1
        fixture.detectChanges();
        expect(queueButton().textContent).toContain('Queue 1 for retraining');

        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2', queued: true })]),
        );
        queueButton().click();
        await fixture.whenStable();
        fixture.detectChanges();

        expect(fake.queueCorrections).toHaveBeenCalledWith({ run_id: 'run-1', sample_ids: ['S2'] });
        expect(text()).toContain('Queued 1 correction for retraining.');
        const statuses = Array.from(
          fixture.nativeElement.querySelectorAll('[data-testid="correction-status"]') as NodeListOf<HTMLElement>,
        ).map((cell) => cell.textContent?.trim());
        expect(statuses).toEqual(['Ready', 'Queued']);
      });

      it('cannot queue a correction that is already queued or has no model recorded', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([
            correction({ sample_id: 'S1', queued: true }),
            correction({ sample_id: 'S2', queueable: false, model_name: null }),
          ]),
        );
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(correctionBoxes().every((box) => box.disabled && !box.checked)).toBe(true);
        expect(queueButton().disabled).toBe(true);
        expect(driftText()).toContain('No model recorded');
        expect(fixture.nativeElement.querySelector('[data-testid="corrections-badge"]')).toBeNull();
      });

      it('shows the server error when queueing is rejected, keeping the selection', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        fake.queueCorrections.mockRejectedValue(new Error('no model recorded for sample(s): S1'));
        await showRun([reviewCase()]);
        await openDriftTab();

        queueButton().click();
        await fixture.whenStable();
        fixture.detectChanges();

        expect(text()).toContain('no model recorded for sample(s): S1');
        expect(queueButton().disabled).toBe(false);
      });

      it('says so when the stored run cannot be read, and when loading fails', async () => {
        fake.getRunDrift.mockResolvedValue(
          runDrift([], { available: false, message: 'The stored run could not be read right now', models: [] }),
        );
        await showRun([reviewCase()]);
        await openDriftTab();
        expect(fixture.nativeElement.querySelector('[data-testid="drift-unavailable"]')?.textContent).toContain(
          'could not be read',
        );

        fake.getRunDrift.mockRejectedValue(new Error('boom'));
        await fixture.componentInstance.refreshDrift();
        fixture.detectChanges();
        expect(fixture.nativeElement.querySelector('[data-testid="drift-error"]')?.textContent).toContain('boom');
      });

      it('sends the run id with a manual drift report and manual flags', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([]));
        await showRun([reviewCase()]);
        await openDriftTab();

        fixture.componentInstance['driftModelName'].set('pcb_body_defect');
        fixture.componentInstance['driftDescription'].set('looks off');
        await fixture.componentInstance.reportDrift();
        expect(fake.reportDrift).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-1' }));

        fixture.componentInstance['selectedSampleIds'].set(new Set(['S1']));
        fixture.componentInstance['retrainingReason'].set('wrong');
        await fixture.componentInstance.flagSelectedForRetraining();
        expect(fake.flagForRetraining).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-1' }));
      });
    });
  });
});
