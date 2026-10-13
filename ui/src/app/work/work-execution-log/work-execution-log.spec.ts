import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { WorkOrchestratorClient, WorkResponderEvent } from '../work-orchestrator-client';
import { WorkService } from '../work.service';
import { WorkExecutionLog } from './work-execution-log';

/** Uses the real WorkService, which appends to its log array in place - the case a fake that
 * swaps in a new array each time would never exercise. */
describe('WorkExecutionLog with the real service', () => {
  function setup() {
    const events$ = new Subject<WorkResponderEvent>();
    const client = {
      run: () => events$.asObservable(),
    } as unknown as WorkOrchestratorClient;
    TestBed.configureTestingModule({
      imports: [WorkExecutionLog],
      providers: [{ provide: WorkOrchestratorClient, useValue: client }],
    });
    const service = TestBed.inject(WorkService);
    const fixture = TestBed.createComponent(WorkExecutionLog);
    fixture.detectChanges();
    const rows = () =>
      Array.from(fixture.nativeElement.querySelectorAll('p[data-kind]')).map(
        (el) => (el as HTMLElement).textContent?.trim() ?? '',
      );
    return { service, fixture, events$, rows };
  }

  const start = (service: WorkService) => service.run('prepare', 'd', 'x', null, {
    featureThreshold: 0.7,
    defectThreshold: 0.7,
    useLlm: false,
    llmModel: '',
    llmFallback: true,
  });

  it('draws lines as they arrive', () => {
    const { service, fixture, events$, rows } = setup();
    start(service);
    events$.next({ type: 'log', text: 'first' });
    fixture.detectChanges();
    events$.next({ type: 'log', text: 'second' });
    fixture.detectChanges();

    const drawn = rows();
    expect(drawn).toHaveLength(2);
    expect(drawn[0]).toContain('first');
    expect(drawn[1]).toContain('second');
  });

  it('shows only the new run after a second run clears the log', () => {
    const { service, fixture, events$, rows } = setup();
    start(service);
    events$.next({ type: 'log', text: 'old' });
    events$.complete();
    fixture.detectChanges();

    const next$ = new Subject<WorkResponderEvent>();
    (TestBed.inject(WorkOrchestratorClient) as unknown as { run: unknown }).run = () =>
      next$.asObservable();
    start(service);
    fixture.detectChanges();
    expect(rows()).toEqual([]);

    next$.next({ type: 'log', text: 'new' });
    fixture.detectChanges();
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toContain('new');
  });
});
