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

    describe('Drift & Retraining: drift from the final decisions, and adding relabels to training data', () => {
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

      function addButton(): HTMLButtonElement {
        return byTestId('add-to-training') as HTMLButtonElement;
      }

      function boxes(): HTMLInputElement[] {
        return Array.from(fixture.nativeElement.querySelectorAll('[data-testid="corrections"] input[type="checkbox"]'));
      }

      async function settle(): Promise<void> {
        await fixture.whenStable();
        fixture.detectChanges();
      }

      it('headlines how much the model drifted in the run, and what that is made of', async () => {
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
        expect(tidy(byTestId('drift-of'))).toBe('1 of 3 it classified'); // the couldn't-decide one is not counted
        expect(tidy(byTestId('kpi-confirmed'))).toBe('2');
        expect(tidy(byTestId('kpi-relabelled'))).toBe('1');
        expect(tidy(byTestId('kpi-no-answer'))).toBe('1');
        const row = fixture.nativeElement.querySelector('[data-model="pcb_body_defect"]') as HTMLElement;
        expect(tidy(row.querySelector('[data-testid="corrected"]'))).toBe('50%');
        // a sample the model couldn't decide is explained, not left as a bare number
        expect(tidy(byTestId('no-model-note'))).toContain('not counted as drift');
      });

      it('says nothing is reviewed yet instead of showing a 0% drift', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(tidy(byTestId('drift-rate'))).toBe('—');
        expect(tidy(byTestId('drift-of'))).toBe('nothing reviewed yet');
        expect(tidy(byTestId('activity-empty'))).toContain('No decisions yet');
        expect(tidy(byTestId('no-corrections'))).toBe('Nothing reviewed yet.');
        expect(addButton().disabled).toBe(true);
      });

      it('shows how the final decisions split and the model\'s commonest mistakes', async () => {
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

        const machine = fixture.nativeElement.querySelector('[data-activity="MACHINE"]') as HTMLElement;
        expect(tidy(machine)).toContain('Accept Machine');
        expect(tidy(machine.querySelector('[data-testid="activity-chosen"]'))).toBe('3');
        expect(tidy(machine)).toContain('(75%)');
        expect(tidy(machine.querySelector('[data-testid="activity-overruled"]'))).toBe('1 where the model was wrong');
        expect(tidy(byTestId('kpi-pending'))).toBe('7');
        expect(allByTestId('top-overruled')[0]).toContain('model said missing part → correct label wrong part');
      });

      it('lists each relabelled sample with what the model said and your label, ticked to add', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        await showRun([reviewCase()]);
        await openDriftTab();

        expect(allByTestId('said-model')).toEqual(['MissingPart', 'MissingPart']);
        expect(allByTestId('chose')).toEqual(['tombstone (MANUAL)', 'tombstone (MANUAL)']);
        expect(allByTestId('correction-status')).toEqual(['Not added yet', 'Not added yet']);
        expect(boxes().every((box) => box.checked)).toBe(true);
        expect(tidy(addButton())).toBe('Add 2 to training data');
        expect(tidy(byTestId('corrections-badge'))).toBe('2 to add');
      });

      it('refetches the drift after the operator saves a final decision', async () => {
        await showRun([reviewCase()]);
        await openCase();
        fake.getRunDrift.mockClear();
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));

        submit().click();
        await settle();

        expect(fake.getRunDrift).toHaveBeenCalledWith('run-1');
        expect(tidy(byTestId('corrections-badge'))).toBe('1 to add');
      });

      it('adds the ticked samples to the training data and puts them in a retraining plan', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction({ sample_id: 'S1' }), correction({ sample_id: 'S2' })]));
        fake.queueCorrections.mockResolvedValue({
          created: [{ id: 't2', sample_ref: 'S2', model_name: 'pcb_body_defect', status: 'open' }],
          already_queued: [],
        });
        await showRun([reviewCase()]);
        await openDriftTab();
        boxes()[0].click(); // untick S1
        fixture.detectChanges();
        expect(tidy(addButton())).toBe('Add 1 to training data');

        fake.getRunDrift.mockResolvedValue(
          runDrift([
            correction({ sample_id: 'S1' }),
            correction({ sample_id: 'S2', queued: true, ticket_status: 'acknowledged' }),
          ]),
        );
        addButton().click();
        await settle();

        // only ids go to the server - it builds the training sample from what it stored
        expect(fake.queueCorrections).toHaveBeenCalledWith({ run_id: 'run-1', sample_ids: ['S2'] });
        expect(fake.draftRetrainingPlan).toHaveBeenCalledWith('pcb_body_defect');
        expect(fixture.nativeElement.textContent).toContain(
          'Added 1 sample to the training data. Retraining plan for pcb_body_defect is waiting for an Admin in the Models tab.',
        );
        expect(allByTestId('correction-status')).toEqual(['Not added yet', 'In a retraining plan']);
      });

      it('cannot add a sample with no model to route to, or one already added', async () => {
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

        expect(boxes().every((box) => box.disabled && !box.checked)).toBe(true);
        expect(addButton().disabled).toBe(true);
        expect(allByTestId('said-model')[1]).toBe("couldn't decide (region: Gap 40%)");
        expect(allByTestId('correction-status')).toEqual([
          'In training data - no plan yet',
          "Can't add - no model to route to",
        ]);
        expect(byTestId('corrections-badge')).toBeNull();
      });

      it('a run where the model couldn\'t decide anything has no drift, and the labels can still be added', async () => {
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

        // not "100% drift": the model never answered
        expect(tidy(byTestId('drift-rate'))).toBe('—');
        expect(tidy(byTestId('drift-of'))).toBe('the model classified none of the reviewed samples');
        expect(tidy(byTestId('kpi-no-answer'))).toBe('2');
        expect(allByTestId('said-model')).toEqual(["couldn't decide (region: Text 50%)", "couldn't decide (region: Text 50%)"]);
        expect(allByTestId('target-model')).toEqual(['pcb_text_defect (region guess)', 'pcb_text_defect (region guess)']);
        expect(boxes().every((box) => box.checked && !box.disabled)).toBe(true);
        expect(tidy(addButton())).toBe('Add 2 to training data');
      });

      it('shows the server error when adding is rejected, keeping the selection', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        fake.queueCorrections.mockRejectedValue(new Error('nothing to add for sample(s) S1'));
        await showRun([reviewCase()]);
        await openDriftTab();

        addButton().click();
        await settle();

        expect(fixture.nativeElement.textContent).toContain('nothing to add for sample(s) S1');
        expect(fake.draftRetrainingPlan).not.toHaveBeenCalled();
        expect(addButton().disabled).toBe(false);
      });

      it('offers to create the plan when samples are in the training data without one', async () => {
        fake.getRunDrift.mockResolvedValue(runDrift([correction()]));
        fake.queueCorrections.mockResolvedValue({
          created: [{ id: 't1', sample_ref: 'S1', model_name: 'pcb_body_defect', status: 'open' }],
          already_queued: [],
        });
        fake.draftRetrainingPlan.mockRejectedValueOnce(new Error('cannot tell which version'));
        await showRun([reviewCase()]);
        await openDriftTab();

        fake.getRunDrift.mockResolvedValue(
          runDrift([correction({ queued: true, ticket_status: 'open' })], { open_tickets: { pcb_body_defect: 1 } }),
        );
        addButton().click();
        await settle();

        expect(fixture.nativeElement.textContent).toContain('No plan could be created for pcb_body_defect yet');
        const leftover = fixture.nativeElement.querySelector('[data-plan-model="pcb_body_defect"]') as HTMLElement;
        expect(tidy(leftover)).toContain('1 sample in the training data but not in a retraining plan yet');

        fake.getRunDrift.mockResolvedValue(runDrift([correction({ queued: true, ticket_status: 'acknowledged' })]));
        (byTestId('draft-plan') as HTMLButtonElement).click();
        await settle();

        expect(fake.draftRetrainingPlan).toHaveBeenLastCalledWith('pcb_body_defect');
        expect(tidy(byTestId('plan-message'))).toContain('Plan drafted for pcb_body_defect');
        expect(fixture.nativeElement.querySelector('[data-plan-model]')).toBeNull();
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
