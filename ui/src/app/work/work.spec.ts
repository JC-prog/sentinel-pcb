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

function fakeWorkService(): {
  service: WorkService;
  log: ReturnType<typeof signal<OrchestratorLogEntry[]>>;
  result: ReturnType<typeof signal<OrchestratorRunResult | null>>;
  run: ReturnType<typeof vi.fn>;
  clearLog: ReturnType<typeof vi.fn>;
  reportDrift: ReturnType<typeof vi.fn>;
  flagForRetraining: ReturnType<typeof vi.fn>;
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

  const service = {
    status: signal(IDLE_STATUS),
    log,
    running: signal(false),
    result,
    uploadDataset: vi.fn().mockResolvedValue('dataset-1'),
    uploadXml: vi.fn().mockResolvedValue('xml-1'),
    uploadImageRootFiles: vi.fn().mockResolvedValue('root-1'),
    run,
    clearLog,
    reportDrift,
    flagForRetraining,
  } as unknown as WorkService;

  return { service, log, result, run, clearLog, reportDrift, flagForRetraining };
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
      fixture.detectChanges();

      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(checkboxes()).toHaveLength(2);
      expect(text).toContain('S1');
      expect(text).toContain('S2');
      expect(text).toContain('pcb_body_defect');
      expect(text).not.toContain('>body<'); // the raw routing key never leaks into the row
    });

    it('a sample that never reached routing shows placeholders, not an error', () => {
      fake.result.set(runResult([sample({ routing: undefined, defect_classification: undefined })]));
      fixture.detectChanges();

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
      fixture.detectChanges();

      const options = Array.from(fixture.nativeElement.querySelectorAll('option'))
        .map((o) => (o as HTMLOptionElement).value)
        .filter((v) => v !== '');
      expect(options).toEqual(['pcb_body_defect', 'pcb_lead_defect']);
    });

    it('reports drift for the chosen model with every sample as evidence, once a model and description are given', async () => {
      fake.result.set(runResult([sample()]));
      fixture.detectChanges();

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
      fixture.detectChanges();

      expect(findButton('Flag for retraining').disabled).toBe(true);

      checkboxes()[0].click();
      fixture.detectChanges();
      expect(findButton('Flag for retraining').disabled).toBe(true); // still no reason

      setTextarea('retrainingReason', 'false positive');
      expect(findButton('Flag for retraining').disabled).toBe(false);
    });

    it('flags only the selected samples, then clears the selection on success', async () => {
      fake.result.set(runResult([sample({ sample_id: 'S1' }), sample({ sample_id: 'S2' })]));
      fixture.detectChanges();
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
      fixture.detectChanges();
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
      fixture.detectChanges();
      checkboxes()[0].click();
      fixture.detectChanges();
      expect(checkedCount()).toBe(1);

      fixture.componentInstance.clearLog();
      fixture.detectChanges();

      expect(checkedCount()).toBe(0);
    });
  });
});
