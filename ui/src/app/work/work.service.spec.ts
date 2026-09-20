import { Subject } from 'rxjs';
import { vi } from 'vitest';
import { WorkOrchestratorClient, WorkResponderEvent } from './work-orchestrator-client';
import { WorkService } from './work.service';

const OPTIONS = {
  featureThreshold: 0.7,
  defectThreshold: 0.7,
  useLlm: false,
  llmModel: '',
  llmFallback: true,
};

function fakeClient(events$: Subject<WorkResponderEvent>): WorkOrchestratorClient {
  return {
    run: () => events$.asObservable(),
    uploadDataset: async () => 'dataset-1',
    uploadXml: async () => 'xml-1',
    uploadImageRootFiles: async () => 'root-1',
  } as unknown as WorkOrchestratorClient;
}

describe('WorkService', () => {
  it('starts with an idle status and empty log', () => {
    const service = new WorkService(fakeClient(new Subject()));
    expect(service.running()).toBe(false);
    expect(service.log()).toEqual([]);
    expect(service.result()).toBeNull();
  });

  it('appends log and plan_step entries in the order they arrive, and marks running while in flight', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);
    expect(service.running()).toBe(true);

    events$.next({ type: 'log', text: 'hello' });
    events$.next({
      type: 'planStep',
      step: {
        plan_version: 1,
        observation: 'o',
        constraint: 'c',
        decision: 'dataset_preparation',
        reason: 'r',
        planner_source: 'deterministic',
        policy: null,
        tool_status: null,
      },
    });

    expect(service.log()).toEqual([
      { kind: 'log', text: 'hello' },
      {
        kind: 'plan_step',
        step: expect.objectContaining({ decision: 'dataset_preparation' }),
      },
    ]);
  });

  it('updates status on a status event', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('prepare', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.next({
      type: 'status',
      status: {
        status: 'RUNNING',
        input_samples: 5,
        preparation_ready: 3,
        verification_passed: 0,
        inference_attempted: 0,
        accepted: 0,
        review_required: 0,
      },
    });

    expect(service.status().input_samples).toBe(5);
    expect(service.status().preparation_ready).toBe(3);
  });

  it('stores the result payload and clears running on completion', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('prepare', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.next({ type: 'result', result: { status: 'PREPARATION_COMPLETED' } });
    events$.complete();

    expect(service.result()).toEqual({ status: 'PREPARATION_COMPLETED' });
    expect(service.running()).toBe(false);
  });

  it('records an error entry and stops running when the stream errors', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.error(new Error('boom'));

    expect(service.log()).toEqual([{ kind: 'error', message: 'boom' }]);
    expect(service.running()).toBe(false);
  });

  it('clearLog resets both the log and the result, not the status', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('prepare', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.next({ type: 'log', text: 'hello' });
    events$.next({ type: 'result', result: { status: 'PREPARATION_COMPLETED' } });
    events$.complete();

    service.clearLog();

    expect(service.log()).toEqual([]);
    expect(service.result()).toBeNull();
  });

  it('ignores a second run() call while one is already in flight', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const client = fakeClient(events$);
    const runSpy = vi.spyOn(client, 'run');
    const service = new WorkService(client);

    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);
    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);

    expect(runSpy).toHaveBeenCalledTimes(1);
  });
});
