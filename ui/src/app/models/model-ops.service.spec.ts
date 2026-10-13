import { vi } from 'vitest';
import { ModelOpsClient } from './model-ops-client';
import { ModelOpsService } from './model-ops.service';
import { drift, job, overview, queue } from './model-ops.fixtures';

type FakeClient = { [K in keyof ModelOpsClient]: ReturnType<typeof vi.fn> };

function fakeClient(): FakeClient {
  return {
    overview: vi.fn().mockResolvedValue(overview()),
    drift: vi.fn().mockResolvedValue(drift()),
    queue: vi.fn().mockResolvedValue(queue()),
    job: vi.fn(),
    approveJob: vi.fn().mockResolvedValue({}),
    submitJob: vi.fn().mockResolvedValue({}),
    cancelJob: vi.fn().mockResolvedValue({}),
    resolveDrift: vi.fn().mockResolvedValue({}),
    promote: vi.fn().mockResolvedValue({}),
    rollback: vi.fn().mockResolvedValue({}),
  } as FakeClient;
}

describe('ModelOpsService', () => {
  let client: FakeClient;
  let service: ModelOpsService;

  beforeEach(() => {
    client = fakeClient();
    service = new ModelOpsService(client as unknown as ModelOpsClient);
  });

  afterEach(() => {
    service.stopPolling();
    vi.useRealTimers();
  });

  describe('refresh', () => {
    it('loads the overview, drift and queue together', async () => {
      await service.refresh();

      expect(service.overview()?.models[0].name).toBe('pcb_body_defect');
      expect(service.drift()?.reports).toHaveLength(1);
      expect(service.queue()?.jobs).toHaveLength(1);
      expect(service.loadError()).toBeNull();
      expect(service.lastUpdated()).not.toBeNull();
      expect(service.loading()).toBe(false);
    });

    it('asks for drift over the window last chosen', async () => {
      await service.setDriftWindow(7);
      await service.refresh();

      expect(client.drift).toHaveBeenLastCalledWith(7);
    });

    it('keeps the last good data and shows an error when a view fails to load', async () => {
      await service.refresh();
      client.queue.mockRejectedValue(new Error('backend down'));
      client.overview.mockResolvedValue(overview({ models: [] }));

      await service.refresh();

      expect(service.loadError()).toBe('backend down');
      expect(service.queue()?.jobs).toHaveLength(1); // unchanged
      expect(service.overview()?.models).toEqual([]); // the views that did load are updated
    });

    it('clears the error on the next successful load', async () => {
      client.overview.mockRejectedValueOnce(new Error('boom'));
      await service.refresh();
      expect(service.loadError()).toBe('boom');

      await service.refresh();

      expect(service.loadError()).toBeNull();
    });

    it('ignores a refresh requested while one is already running', async () => {
      const first = service.refresh();
      const second = service.refresh();
      await Promise.all([first, second]);

      expect(client.overview).toHaveBeenCalledTimes(1);
    });
  });

  describe('inference status', () => {
    it('is null until something has loaded, then follows the overview', async () => {
      expect(service.inference()).toBeNull();

      client.overview.mockResolvedValue(
        overview({ inference: { configured: true, reachable: false, error: 'down' } }),
      );
      await service.refresh();

      expect(service.inference()).toEqual({ configured: true, reachable: false, error: 'down' });
    });
  });

  describe('polling', () => {
    beforeEach(() => vi.useFakeTimers());

    it('refreshes on an interval only while a job is still moving', async () => {
      client.queue.mockResolvedValue(queue({ jobs: [job({ status: 'running' })] }));
      await service.refresh();
      expect(service.hasActiveJobs()).toBe(true);
      client.overview.mockClear();

      service.startPolling(1000);
      await vi.advanceTimersByTimeAsync(3000);

      expect(client.overview).toHaveBeenCalledTimes(3);
    });

    it('does not poll when nothing is in progress', async () => {
      client.queue.mockResolvedValue(queue({ jobs: [job({ status: 'pending_approval' })] }));
      await service.refresh();
      expect(service.hasActiveJobs()).toBe(false);
      client.overview.mockClear();

      service.startPolling(1000);
      await vi.advanceTimersByTimeAsync(5000);

      expect(client.overview).not.toHaveBeenCalled();
    });

    it('stops polling when told to, and starting twice does not double it up', async () => {
      client.queue.mockResolvedValue(queue({ jobs: [job({ status: 'queued' })] }));
      await service.refresh();
      client.overview.mockClear();

      service.startPolling(1000);
      service.startPolling(1000);
      await vi.advanceTimersByTimeAsync(1000);
      expect(client.overview).toHaveBeenCalledTimes(1);

      service.stopPolling();
      await vi.advanceTimersByTimeAsync(5000);
      expect(client.overview).toHaveBeenCalledTimes(1);
    });
  });

  describe('actions', () => {
    it('runs the action, then reloads so the page shows the real outcome', async () => {
      const ok = await service.approveJob('j-1');

      expect(ok).toBe(true);
      expect(client.approveJob).toHaveBeenCalledWith('j-1');
      expect(client.queue).toHaveBeenCalledTimes(1); // the reload
      expect(service.actionError()).toBeNull();
    });

    it('routes each action to the right call', async () => {
      await service.submitJob('j-1');
      await service.cancelJob('j-2');
      await service.resolveDrift('r-1');
      await service.promote('pcb_body_defect', 'JcProg/body@v2');
      await service.rollback('pcb_body_defect');

      expect(client.submitJob).toHaveBeenCalledWith('j-1');
      expect(client.cancelJob).toHaveBeenCalledWith('j-2');
      expect(client.resolveDrift).toHaveBeenCalledWith('r-1');
      expect(client.promote).toHaveBeenCalledWith('pcb_body_defect', 'JcProg/body@v2');
      expect(client.rollback).toHaveBeenCalledWith('pcb_body_defect');
    });

    it("surfaces the server's refusal and still reloads", async () => {
      client.promote.mockRejectedValue(new Error('inference service refused'));

      const ok = await service.promote('pcb_body_defect', 'JcProg/body@v2');

      expect(ok).toBe(false);
      expect(service.actionError()).toBe('inference service refused');
      expect(client.overview).toHaveBeenCalled();
    });

    it('clears a previous error when the next action starts, or on demand', async () => {
      client.cancelJob.mockRejectedValueOnce(new Error('nope'));
      await service.cancelJob('j-1');
      expect(service.actionError()).toBe('nope');

      await service.cancelJob('j-1');
      expect(service.actionError()).toBeNull();

      client.cancelJob.mockRejectedValueOnce(new Error('again'));
      await service.cancelJob('j-1');
      service.clearActionError();
      expect(service.actionError()).toBeNull();
    });

    it('marks the target busy while an action runs and refuses a second one on it', async () => {
      let release: () => void = () => undefined;
      client.approveJob.mockReturnValue(new Promise<void>((resolve) => (release = resolve)));

      const first = service.approveJob('j-1');
      expect(service.isBusy('job:j-1')).toBe(true);
      expect(await service.approveJob('j-1')).toBe(false); // ignored - already in flight
      expect(client.approveJob).toHaveBeenCalledTimes(1);
      expect(service.isBusy('job:j-2')).toBe(false); // other targets are unaffected

      release();
      await first;
      expect(service.isBusy('job:j-1')).toBe(false);
    });

    it('reloads after an action even if a poll was mid-flight', async () => {
      let finishPoll: () => void = () => undefined;
      client.overview.mockReturnValueOnce(
        new Promise((resolve) => (finishPoll = () => resolve(overview()))),
      );
      const poll = service.refresh(); // in flight

      const action = service.approveJob('j-1');
      await Promise.resolve();
      finishPoll();
      await Promise.all([poll, action]);

      expect(client.overview).toHaveBeenCalledTimes(2); // the poll, then the post-action reload
    });
  });

  it('loads one job in full without touching the page state', async () => {
    client.job.mockResolvedValue({ id: 'j-1', samples: [] });

    expect(await service.loadJob('j-1')).toEqual({ id: 'j-1', samples: [] });
    expect(service.queue()).toBeNull();
  });
});
