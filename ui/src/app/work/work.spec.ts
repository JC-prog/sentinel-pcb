import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { vi } from 'vitest';
import { Work } from './work';
import { WorkService } from '../work.service';
import { OrchestratorLogEntry, OrchestratorStatusEvent } from '../models/orchestrator.models';

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
  result: ReturnType<typeof signal<unknown>>;
  run: ReturnType<typeof vi.fn>;
  clearLog: ReturnType<typeof vi.fn>;
} {
  const log = signal<OrchestratorLogEntry[]>([]);
  const result = signal<unknown>(null);
  const run = vi.fn();
  const clearLog = vi.fn(() => {
    log.set([]);
    result.set(null);
  });

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
  } as unknown as WorkService;

  return { service, log, result, run, clearLog };
}

function file(name: string): File {
  return new File(['x'], name);
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
    fake.result.set({ status: 'PREPARATION_COMPLETED' });
    fixture.detectChanges();

    const downloadButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLButtonElement).textContent?.includes('Download'),
    ) as HTMLButtonElement;

    expect(downloadButton.textContent).toContain('Download result.json');
  });
});
