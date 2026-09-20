import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { AuthService, AuthUser } from '../auth.service';
import { Models } from './models';
import {
  DriftOverview,
  ModelsOverview,
  QueueOverview,
  RetrainingJobDetail,
} from './model-ops.models';
import { ModelOpsService } from './model-ops.service';
import {
  drift,
  job,
  jobDetail,
  model,
  overview,
  queue,
  report,
  version,
} from './model-ops.fixtures';

const ADMIN: AuthUser = {
  id: 'u-admin',
  username: 'ada-admin',
  email: 'ada@example.com',
  employeeId: 'EMP-1',
  departmentShift: 'Day',
  role: 'admin',
};
const QA: AuthUser = { ...ADMIN, id: 'u-qa', username: 'jane-qa', role: 'qa' };

function fakeService() {
  const overviewSignal = signal<ModelsOverview | null>(overview());
  const driftSignal = signal<DriftOverview | null>(drift());
  const queueSignal = signal<QueueOverview | null>(queue());
  const busy = signal<ReadonlySet<string>>(new Set());
  const service = {
    overview: overviewSignal,
    drift: driftSignal,
    queue: queueSignal,
    loading: signal(false),
    loadError: signal<string | null>(null),
    actionError: signal<string | null>(null),
    lastUpdated: signal<Date | null>(null),
    inference: signal(overview().inference as ModelsOverview['inference'] | null),
    refresh: vi.fn().mockResolvedValue(undefined),
    setDriftWindow: vi.fn().mockResolvedValue(undefined),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
    clearActionError: vi.fn(),
    isBusy: (key: string) => busy().has(key),
    loadJob: vi.fn(),
    approveJob: vi.fn().mockResolvedValue(true),
    submitJob: vi.fn().mockResolvedValue(true),
    cancelJob: vi.fn().mockResolvedValue(true),
    resolveDrift: vi.fn().mockResolvedValue(true),
    promote: vi.fn().mockResolvedValue(true),
    rollback: vi.fn().mockResolvedValue(true),
  };
  return { service, overviewSignal, driftSignal, queueSignal, busy };
}

describe('Models', () => {
  let fixture: ComponentFixture<Models>;
  let fake: ReturnType<typeof fakeService>;
  let currentUser: ReturnType<typeof signal<AuthUser | null | undefined>>;

  async function create(user: AuthUser = ADMIN): Promise<void> {
    fake = fakeService();
    currentUser = signal<AuthUser | null | undefined>(user);
    await TestBed.configureTestingModule({
      imports: [Models],
      providers: [
        { provide: ModelOpsService, useValue: fake.service },
        { provide: AuthService, useValue: { currentUser } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Models);
    fixture.detectChanges();
  }

  const root = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (): string => root().textContent ?? '';

  function button(label: string): HTMLButtonElement | undefined {
    return Array.from(root().querySelectorAll('button')).find((b) =>
      b.textContent?.trim().startsWith(label),
    ) as HTMLButtonElement | undefined;
  }

  async function click(label: string): Promise<void> {
    const target = button(label);
    expect(target, `a "${label}" button`).toBeDefined();
    target!.click();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  function setOverview(value: ModelsOverview): void {
    fake.overviewSignal.set(value);
    fixture.detectChanges();
  }

  function setQueue(value: QueueOverview): void {
    fake.queueSignal.set(value);
    fixture.detectChanges();
  }

  afterEach(() => vi.restoreAllMocks());

  describe('lifecycle', () => {
    beforeEach(() => create());

    it('loads on open and starts polling, then stops when left', () => {
      expect(fake.service.refresh).toHaveBeenCalledTimes(1);
      expect(fake.service.startPolling).toHaveBeenCalledTimes(1);

      fixture.destroy();

      expect(fake.service.stopPolling).toHaveBeenCalledTimes(1);
    });

    it('refreshes on demand', async () => {
      await click('Refresh');

      expect(fake.service.refresh).toHaveBeenCalledTimes(2);
    });
  });

  describe('banners', () => {
    beforeEach(() => create());

    it('says when the inference service is not configured', () => {
      fake.service.inference.set({ configured: false, reachable: false, error: null });
      fixture.detectChanges();

      expect(text()).toContain('inference service is not configured');
    });

    it("says when it can't be reached, with the reason", () => {
      fake.service.inference.set({ configured: true, reachable: false, error: 'connect refused' });
      fixture.detectChanges();

      expect(text()).toContain("can't be reached");
      expect(text()).toContain('connect refused');
    });

    it('shows nothing about inference when it is healthy', () => {
      expect(text()).not.toContain('not configured');
      expect(text()).not.toContain("can't be reached");
    });

    it('shows a load error and an action error, which can be dismissed', async () => {
      fake.service.loadError.set('Could not load');
      fake.service.actionError.set('inference service refused');
      fixture.detectChanges();

      const alerts = Array.from(root().querySelectorAll('[role="alert"]')).map((el) =>
        el.textContent?.trim(),
      );
      expect(alerts.some((a) => a?.includes('Could not load'))).toBe(true);
      expect(alerts.some((a) => a?.includes('inference service refused'))).toBe(true);

      await click('Dismiss');
      expect(fake.service.clearActionError).toHaveBeenCalled();
    });
  });

  describe('models', () => {
    beforeEach(() => create());

    it('shows each model with its live version, labels and open counts', () => {
      setOverview(
        overview({
          models: [
            model({
              live: version({ activated_by: 'ada-admin' }),
              open_drift_reports: 2,
              open_tickets: 5,
              active_jobs: 1,
            }),
          ],
        }),
      );

      const card = root().querySelector('[data-model="pcb_body_defect"]') as HTMLElement;
      expect(card.textContent).toContain('JcProg/body@v1');
      expect(card.textContent).toContain('activated by ada-admin');
      expect(card.textContent).toContain('MissingPart');
      expect(card.querySelector('[data-testid="open-drift"]')?.textContent?.trim()).toBe('2');
      expect(card.querySelector('[data-testid="open-tickets"]')?.textContent?.trim()).toBe('5');
      expect(card.querySelector('[data-testid="active-jobs"]')?.textContent?.trim()).toBe('1');
    });

    it('says so when a model has no live version or nothing is recorded', () => {
      setOverview(overview({ models: [model({ live: null })] }));
      expect(text()).toContain('No live version recorded');

      setOverview(overview({ models: [] }));
      expect(text()).toContain('No models recorded yet');
    });

    it('lists the version history on demand, flagging candidates that came from a retrain', async () => {
      const candidate = version({
        id: 'v-2',
        version: 'JcProg/body@v2',
        revision: 'v2',
        status: 'candidate',
        source_job_id: 'j-9',
        activated_at: null,
      });
      setOverview(overview({ models: [model({ versions: [version(), candidate] })] }));
      expect(text()).not.toContain('JcProg/body@v2');

      await click('Show versions (2)');

      expect(text()).toContain('JcProg/body@v2');
      expect(text()).toContain('from a retraining job');
      await click('Hide versions (2)');
      expect(text()).not.toContain('JcProg/body@v2');
    });

    describe('as an Admin', () => {
      const withCandidate = (): void => {
        const previous = version({
          id: 'v-0',
          version: 'JcProg/body@v0',
          revision: 'v0',
          status: 'previous',
        });
        const candidate = version({
          id: 'v-2',
          version: 'JcProg/body@v2',
          revision: 'v2',
          status: 'candidate',
        });
        setOverview(
          overview({
            models: [model({ previous, versions: [version(), previous, candidate] })],
          }),
        );
      };

      it('can make a non-live version live, after confirming', async () => {
        withCandidate();
        await click('Show versions');
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);

        const makeLive = Array.from(root().querySelectorAll('button')).filter(
          (b) => b.textContent?.trim() === 'Make live',
        );
        expect(makeLive).toHaveLength(2); // the previous and the candidate - never the live one
        makeLive[1].click();
        await fixture.whenStable();

        expect(confirm).toHaveBeenCalledWith(expect.stringContaining('JcProg/body@v2'));
        expect(fake.service.promote).toHaveBeenCalledWith('pcb_body_defect', 'JcProg/body@v2');
      });

      it('does nothing if the confirmation is declined', async () => {
        withCandidate();
        await click('Show versions');
        vi.spyOn(window, 'confirm').mockReturnValue(false);

        const makeLive = Array.from(root().querySelectorAll('button')).find(
          (b) => b.textContent?.trim() === 'Make live',
        );
        makeLive!.click();
        await fixture.whenStable();

        expect(fake.service.promote).not.toHaveBeenCalled();
      });

      it('can roll back to the previous version', async () => {
        withCandidate();
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);

        await click('Roll back to v0');

        expect(confirm).toHaveBeenCalledWith(expect.stringContaining('JcProg/body@v0'));
        expect(fake.service.rollback).toHaveBeenCalledWith('pcb_body_defect');
      });

      it('is not offered a rollback when there is no previous version', () => {
        expect(button('Roll back')).toBeUndefined();
      });

      it('has its buttons disabled while an action on that model is running', () => {
        withCandidate();
        fake.busy.set(new Set(['model:pcb_body_defect']));
        fixture.detectChanges();

        expect(button('Roll back')!.disabled).toBe(true);
      });
    });

    describe('as QA', () => {
      beforeEach(async () => {
        TestBed.resetTestingModule();
        await create(QA);
        const previous = version({ id: 'v-0', version: 'JcProg/body@v0', status: 'previous' });
        setOverview(overview({ models: [model({ previous, versions: [version(), previous] })] }));
      });

      it('can read the versions but is offered no way to change them', async () => {
        await click('Show versions');

        expect(text()).toContain('JcProg/body@v0');
        expect(button('Make live')).toBeUndefined();
        expect(button('Roll back')).toBeUndefined();
      });
    });
  });

  describe('drift', () => {
    beforeEach(() => create());

    it("shows a report with who filed it, the snapshot's numbers, its signals and cases", () => {
      const card = root().querySelector('[data-report="r-1"]') as HTMLElement;

      expect(card.textContent).toContain('jane-qa');
      expect(card.textContent).toContain('False Tombstone calls since Monday');
      expect(card.textContent).toContain('12 cases in window');
      expect(card.textContent).toContain('override rate 80%');
      expect(card.textContent).toContain('mean confidence 0.82');
      expect(card.textContent).toContain('override rate rose from 0% to 80%');
      expect(card.textContent).toContain('CASE-000007');
    });

    it('summarises reported and open counts per model', () => {
      const counts = root().querySelector('[data-testid="drift-counts"]')!.textContent!;

      expect(counts).toContain('pcb_body_defect');
      expect(counts).toContain('1 reported, 1 open');
    });

    it('re-queries when another window is chosen', async () => {
      await click('7 days');

      expect(fake.service.setDriftWindow).toHaveBeenCalledWith(7);
      expect(button('7 days')!.getAttribute('aria-pressed')).toBe('true');
      expect(button('30 days')!.getAttribute('aria-pressed')).toBe('false');
    });

    it('lets an Admin resolve an open report but not a resolved one', async () => {
      await click('Mark resolved');
      expect(fake.service.resolveDrift).toHaveBeenCalledWith('r-1');

      fake.driftSignal.set(drift({ reports: [report({ status: 'resolved' })] }));
      fixture.detectChanges();
      expect(button('Mark resolved')).toBeUndefined();
    });

    it('says so when there are no reports, and tolerates a report without stats', () => {
      fake.driftSignal.set(drift({ reports: [], reported_in_window_by_model: {} }));
      fixture.detectChanges();
      expect(text()).toContain('No drift reports');

      fake.driftSignal.set(drift({ reports: [report({ stats: {}, case_numbers: [] })] }));
      fixture.detectChanges();
      expect(text()).toContain('False Tombstone calls since Monday');
      expect(text()).not.toContain('cases in window');
    });

    it('does not offer QA a resolve button', async () => {
      TestBed.resetTestingModule();
      await create(QA);

      expect(button('Mark resolved')).toBeUndefined();
    });
  });

  describe('retraining queue', () => {
    beforeEach(() => create());

    it('shows the flagged-case counts and each job with its status, source and author', () => {
      expect(root().querySelector('[data-testid="ticket-counts"]')?.textContent).toContain(
        '1 unplanned · 2 in a plan · 3 resolved',
      );
      const card = root().querySelector('[data-job="j-1"]') as HTMLElement;
      expect(card.textContent).toContain('Pending approval');
      expect(card.textContent).toContain('JcProg/body@v1');
      expect(card.textContent).toContain('2 flagged cases');
      expect(card.textContent).toContain('drafted by jane-qa');
      expect(card.textContent).toContain('Retrain on flagged false positives.');
    });

    it('has an empty state', () => {
      setQueue(queue({ jobs: [] }));

      expect(text()).toContain('No retraining jobs yet');
    });

    it('lets an Admin approve a pending plan, after confirming', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);

      await click('Approve');

      expect(confirm).toHaveBeenCalledWith(expect.stringContaining('2 flagged cases'));
      expect(fake.service.approveJob).toHaveBeenCalledWith('j-1');
    });

    it('does not approve if the confirmation is declined', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(false);

      await click('Approve');

      expect(fake.service.approveJob).not.toHaveBeenCalled();
    });

    it('offers to retry sending an approved job that never reached the service', async () => {
      setQueue(
        queue({
          jobs: [job({ status: 'approved', error: 'approved, but not sent to the inference service' })],
        }),
      );

      expect(text()).toContain('Approved – not sent');
      expect(text()).toContain('not sent to the inference service');
      expect(button('Approve')).toBeUndefined();
      await click('Retry sending');
      expect(fake.service.submitJob).toHaveBeenCalledWith('j-1');
    });

    it('shows progress for a job that is running', () => {
      setQueue(queue({ jobs: [job({ status: 'running', progress: 0.4 })] }));

      const bar = root().querySelector('[role="progressbar"]')!;
      expect(bar.getAttribute('aria-valuenow')).toBe('40');
      expect(text()).toContain('Running');
    });

    it('does not show a progress bar for a job that is not moving', () => {
      expect(root().querySelector('[role="progressbar"]')).toBeNull();
    });

    it('lets an Admin cancel any unfinished job but nothing that has finished', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      setQueue(queue({ jobs: [job({ status: 'running' })] }));
      await click('Cancel');
      expect(fake.service.cancelJob).toHaveBeenCalledWith('j-1');

      setQueue(queue({ jobs: [job({ status: 'succeeded' })] }));
      expect(button('Cancel')).toBeUndefined();
      expect(button('Approve')).toBeUndefined();
    });

    it("marks a simulated success as such rather than as an improved model", () => {
      setQueue(queue({ jobs: [job({ status: 'succeeded', simulated: true })] }));

      expect(root().querySelector('[data-testid="simulated-note"]')?.textContent).toContain(
        'no new model was produced',
      );
    });

    it('names the weights a real success produced', () => {
      setQueue(
        queue({
          jobs: [
            job({
              status: 'succeeded',
              simulated: false,
              artifact_repo_id: 'JcProg/body',
              artifact_revision: 'v2',
            }),
          ],
        }),
      );

      expect(text()).toContain('JcProg/body@v2');
      expect(root().querySelector('[data-testid="simulated-note"]')).toBeNull();
    });

    it('shows why a job failed', () => {
      setQueue(queue({ jobs: [job({ status: 'failed', error: 'out of memory' })] }));

      expect(text()).toContain('Failed');
      expect(text()).toContain('out of memory');
    });

    it('expands a job to show the cases it was drafted from, and collapses it again', async () => {
      fake.service.loadJob.mockResolvedValue(jobDetail() as RetrainingJobDetail);

      await click('Show flagged cases');

      // scoped to the job: the drift report above also mentions CASE-000007
      const card = (): string => root().querySelector('[data-job="j-1"]')!.textContent!;
      expect(fake.service.loadJob).toHaveBeenCalledWith('j-1');
      expect(card()).toContain('CASE-000007');
      expect(card()).toContain('MissingPart');
      expect(card()).toContain('Golden');
      expect(card()).toContain('c-2'); // no case number recorded - falls back to the id
      await click('Hide flagged cases');
      expect(card()).not.toContain('CASE-000007');
    });

    it('says so when the cases cannot be loaded', async () => {
      fake.service.loadJob.mockRejectedValue(new Error('boom'));

      await click('Show flagged cases');

      expect(text()).toContain('Could not load the cases');
    });

    describe('as QA', () => {
      beforeEach(async () => {
        TestBed.resetTestingModule();
        await create(QA);
      });

      it('cannot approve or resend', () => {
        setQueue(queue({ jobs: [job(), job({ id: 'j-2', status: 'approved' })] }));

        expect(button('Approve')).toBeUndefined();
        expect(button('Retry sending')).toBeUndefined();
      });

      it('can withdraw a plan they drafted while it is pending, and only that', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        setQueue(
          queue({
            jobs: [
              job({ id: 'mine', created_by: 'jane-qa' }),
              job({ id: 'theirs', created_by: 'someone-else' }),
              job({ id: 'sent', created_by: 'jane-qa', status: 'queued' }),
            ],
          }),
        );

        const withdraws = Array.from(root().querySelectorAll('button')).filter((b) =>
          b.textContent?.trim().startsWith('Withdraw'),
        );
        expect(withdraws).toHaveLength(1);
        expect(button('Cancel')).toBeUndefined();

        withdraws[0].click();
        await fixture.whenStable();

        expect(fake.service.cancelJob).toHaveBeenCalledWith('mine');
      });
    });
  });
});
