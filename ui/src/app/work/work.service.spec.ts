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
    listRuns: async () => ({ runs: [], available: true }),
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
      { kind: 'log', text: 'hello', at: expect.any(Number) },
      {
        kind: 'plan_step',
        step: expect.objectContaining({ decision: 'dataset_preparation' }),
        at: expect.any(Number),
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
    events$.next({
      type: 'result',
      result: { workflow_status: 'PREPARATION_COMPLETED', termination_reason: null, results: [] },
    });
    events$.complete();

    expect(service.result()).toEqual({
      workflow_status: 'PREPARATION_COMPLETED',
      termination_reason: null,
      results: [],
    });
    expect(service.running()).toBe(false);
  });

  it('records an error entry and stops running when the stream errors', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.error(new Error('boom'));

    expect(service.log()).toEqual([{ kind: 'error', message: 'boom', at: expect.any(Number) }]);
    expect(service.running()).toBe(false);
  });

  it('clearLog resets both the log and the result, not the status', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));

    service.run('prepare', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.next({ type: 'log', text: 'hello' });
    events$.next({
      type: 'result',
      result: { workflow_status: 'PREPARATION_COMPLETED', termination_reason: null, results: [] },
    });
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

  it('cancel stops the run, keeps what was logged and lets a new run start', () => {
    const events$ = new Subject<WorkResponderEvent>();
    const service = new WorkService(fakeClient(events$));
    service.run('run_full', 'dataset-1', 'xml-1', null, OPTIONS);
    events$.next({ type: 'log', text: 'hello' });

    service.cancel();
    events$.next({ type: 'log', text: 'after cancel' });

    expect(service.running()).toBe(false);
    expect(events$.observed).toBe(false);
    expect(service.log().map((entry) => (entry.kind === 'log' ? entry.text : ''))).toEqual([
      'hello',
      'Run cancelled.',
    ]);
  });

  it('cancel does nothing when no run is in flight', () => {
    const service = new WorkService(fakeClient(new Subject()));
    service.cancel();
    expect(service.log()).toEqual([]);
  });

  it('starts each run with an empty log and fresh counters', () => {
    const first$ = new Subject<WorkResponderEvent>();
    const client = fakeClient(first$);
    const service = new WorkService(client);
    service.run('prepare', 'dataset-1', 'xml-1', null, OPTIONS);
    first$.next({ type: 'log', text: 'old line' });
    first$.next({ type: 'status', status: { ...service.status(), status: 'READY', input_samples: 9 } });
    first$.complete();

    const second$ = new Subject<WorkResponderEvent>();
    client.run = () => second$.asObservable();
    service.run('prepare_verify', 'dataset-1', 'xml-1', null, OPTIONS);

    expect(service.log()).toEqual([]);
    expect(service.status().input_samples).toBe(0);
    second$.next({ type: 'log', text: 'new line' });
    expect(service.log().map((e) => (e.kind === 'log' ? e.text : ''))).toEqual(['new line']);
  });

  describe('run history', () => {
    const STORED = {
      run_id: 'run-old',
      workflow_status: 'REVIEW_REQUIRED',
      termination_reason: null,
      results: [],
      errors: [],
      input_samples: 12,
      accepted: 7,
      review_required: 5,
      verification_failed: 2,
    };

    function clientWith(overrides: Record<string, unknown>): WorkOrchestratorClient {
      return { ...fakeClient(new Subject()), ...overrides } as unknown as WorkOrchestratorClient;
    }

    it('loads the stored runs', async () => {
      const runs = [{ run_id: 'r1', review_required: 2 }];
      const service = new WorkService(
        clientWith({ listRuns: async () => ({ runs, available: true }) }),
      );

      await service.loadRuns();

      expect(service.runs()).toEqual(runs);
      expect(service.runsAvailable()).toBe(true);
    });

    it('says when the server could not read its run store', async () => {
      const service = new WorkService(
        clientWith({ listRuns: async () => ({ runs: [], available: false }) }),
      );

      await service.loadRuns();

      expect(service.runs()).toEqual([]);
      expect(service.runsAvailable()).toBe(false);
    });

    it('keeps the last list and reports the error when loading fails', async () => {
      const service = new WorkService(
        clientWith({ listRuns: async () => { throw new Error('boom'); } }),
      );

      await service.loadRuns();

      expect(service.runsError()).toBe('boom');
    });

    it('opens a stored run as if it had just finished', async () => {
      const service = new WorkService(clientWith({ getRun: async () => STORED }));

      await service.openRun('run-old');

      expect(service.result()?.run_id).toBe('run-old');
      expect(service.status()).toMatchObject({
        input_samples: 12,
        accepted: 7,
        review_required: 5,
        verification_failed: 2,
      });
      expect(service.log()).toEqual([]);
    });

    it('does not replace a run that is in progress', async () => {
      const getRun = vi.fn().mockResolvedValue(STORED);
      const service = new WorkService(clientWith({ getRun, run: () => new Subject().asObservable() }));
      service.run('run_full', 'd', 'x', null, OPTIONS);

      await service.openRun('run-old');

      expect(getRun).not.toHaveBeenCalled();
      expect(service.result()).toBeNull();
    });

    it('reports a run that cannot be opened and leaves the current result alone', async () => {
      const service = new WorkService(
        clientWith({ getRun: async () => { throw new Error('run nope is not stored'); } }),
      );

      await service.openRun('nope');

      expect(service.runsError()).toBe('run nope is not stored');
      expect(service.result()).toBeNull();
    });
  });
});
