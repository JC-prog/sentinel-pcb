import { firstValueFrom } from 'rxjs';
import { toArray } from 'rxjs/operators';
import { vi } from 'vitest';
import { AuthService } from '../auth.service';
import { WorkOrchestratorClient } from './work-orchestrator-client';

function sseResponse(body: string, ok = true): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return { ok, status: ok ? 200 : 500, body: stream } as unknown as Response;
}

/** WorkOrchestratorClient only ever calls fetchWithAuth() - a minimal stand-in avoids pulling in
 * AuthService's own Router dependency, same approach as http-chat-responder.spec.ts. */
function fakeAuthService(): AuthService {
  return {
    fetchWithAuth: (input: string, init: RequestInit = {}) =>
      fetch(input, { ...init, credentials: 'include' }),
  } as AuthService;
}

describe('WorkOrchestratorClient', () => {
  let authService: AuthService;

  beforeEach(() => {
    authService = fakeAuthService();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('emits log, status, planStep and result events in order, then completes', async () => {
    const body =
      'event: log\ndata: {"text":"=== DATASET PREPARATION ==="}\n\n' +
      'event: status\ndata: {"status":"RUNNING","input_samples":1,"preparation_ready":1,' +
      '"verification_passed":0,"inference_attempted":0,"accepted":0,"review_required":0}\n\n' +
      'event: plan_step\ndata: {"plan_version":1,"observation":"o","constraint":"c","decision":' +
      '"dataset_preparation","reason":"r","planner_source":"deterministic","policy":null,"tool_status":null}\n\n' +
      'event: result\ndata: {"status":"PREPARATION_COMPLETED"}\n\n' +
      'event: done\ndata: {}\n\n';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(body)));

    const events = await firstValueFrom(
      new WorkOrchestratorClient(authService)
        .run({
          mode: 'prepare',
          dataset_id: 'd1',
          xml_id: 'x1',
          feature_threshold: 0.7,
          defect_threshold: 0.7,
          use_llm: false,
          llm_fallback: true,
        })
        .pipe(toArray()),
    );

    expect(events.map((e) => e.type)).toEqual(['log', 'status', 'planStep', 'result']);
  });

  it('errors the observable when the stream sends an error frame', async () => {
    const body = 'event: error\ndata: {"message":"upstream failed"}\n\n';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(body)));

    const events = await firstValueFrom(
      new WorkOrchestratorClient(authService)
        .run({
          mode: 'run_full',
          dataset_id: 'd1',
          xml_id: 'x1',
          feature_threshold: 0.7,
          defect_threshold: 0.7,
          use_llm: false,
          llm_fallback: true,
        })
        .pipe(toArray()),
    );

    expect(events).toEqual([{ type: 'error', message: 'upstream failed' }]);
  });

  it('uploads a dataset CSV and returns its id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ id: 'ds-1' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const id = await new WorkOrchestratorClient(authService).uploadDataset(
      new File(['a,b'], 'dataset.csv', { type: 'text/csv' }),
    );

    expect(id).toBe('ds-1');
    expect(fetchMock.mock.calls[0][0]).toContain('/api/orchestrator/uploads/dataset');
  });

  it('uploads image-root files with their relative paths', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ id: 'root-1' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'image.jpg', { type: 'image/jpeg' });
    const id = await new WorkOrchestratorClient(authService).uploadImageRootFiles([file]);

    expect(id).toBe('root-1');
    const formData = fetchMock.mock.calls[0][1].body as FormData;
    expect(formData.get('files')).toBe(file);
    expect(formData.get('relative_paths')).toBe('image.jpg');
  });

  const SAMPLE = { sample_id: 'S1', final_decision: 'REVIEW_REQUIRED' as const };

  it('posts a JSON body to the drift-report route and returns the parsed response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ id: 'r1', model_name: 'pcb_body_defect', model_version: null, status: 'open' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const result = await new WorkOrchestratorClient(authService).reportDrift({
      model_name: 'pcb_body_defect',
      description: 'looks off',
      samples: [SAMPLE],
    });

    expect(result).toEqual({ id: 'r1', model_name: 'pcb_body_defect', model_version: null, status: 'open' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/orchestrator/monitoring/drift-report');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body)).toEqual({
      model_name: 'pcb_body_defect',
      description: 'looks off',
      samples: [SAMPLE],
    });
  });

  it('posts the selected tickets to the retraining-tickets route', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{ id: 't1', sample_ref: 'S1', model_name: 'pcb_body_defect', status: 'open' }]),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const result = await new WorkOrchestratorClient(authService).flagForRetraining({
      tickets: [{ sample: SAMPLE, reason: 'false positive' }],
    });

    expect(result).toEqual([{ id: 't1', sample_ref: 'S1', model_name: 'pcb_body_defect', status: 'open' }]);
    expect(fetchMock.mock.calls[0][0]).toContain('/api/orchestrator/monitoring/retraining-tickets');
  });

  it('fetches the server-computed drift of a run', async () => {
    const drift = { run_id: 'run 1', available: true, totals: {}, models: [], corrections: [] };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(drift) } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const result = await new WorkOrchestratorClient(authService).getRunDrift('run 1');

    expect(result).toEqual(drift);
    expect(fetchMock.mock.calls[0][0]).toContain('/api/orchestrator/monitoring/run-drift?run_id=run%201');
  });

  it('posts only ids to queue the operator corrections, and surfaces a rejection', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ created: [{ id: 't1' }], already_queued: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'no operator correction recorded for sample(s): S2' }),
      } as Response);
    vi.stubGlobal('fetch', fetchMock);
    const client = new WorkOrchestratorClient(authService);

    await client.queueCorrections({ run_id: 'run-1', sample_ids: ['S1'] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/orchestrator/monitoring/run-retraining-tickets');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ run_id: 'run-1', sample_ids: ['S1'] });

    await expect(client.queueCorrections({ run_id: 'run-1', sample_ids: ['S2'] })).rejects.toThrow(
      'no operator correction recorded for sample(s): S2',
    );
  });

  it('posts the model to draft a retraining plan for, and surfaces a refusal', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ job_id: 'j1', status: 'pending_approval', sample_count: 2 }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: 'no open retraining tickets for pcb_body_defect' }),
      } as Response);
    vi.stubGlobal('fetch', fetchMock);
    const client = new WorkOrchestratorClient(authService);

    expect((await client.draftRetrainingPlan('pcb_body_defect')).job_id).toBe('j1');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/orchestrator/monitoring/retraining-plan');
    expect(JSON.parse(init.body)).toEqual({ model_name: 'pcb_body_defect' });

    await expect(client.draftRetrainingPlan('pcb_body_defect')).rejects.toThrow(
      'no open retraining tickets for pcb_body_defect',
    );
  });

  it('fetches the review cases of a run', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{ sample_id: 'S1', review: null, decision: null }]),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    const result = await new WorkOrchestratorClient(authService).getReviewCases('run 1');

    expect(result).toEqual([{ sample_id: 'S1', review: null, decision: null }]);
    expect(fetchMock.mock.calls[0][0]).toContain('/api/orchestrator/reviews/cases?run_id=run%201');
  });

  it('turns a review image into an object URL, or null when the server has none', async () => {
    const blob = new Blob(['x']);
    const createObjectURL = vi.fn().mockReturnValue('blob:img');
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true, writable: true });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, blob: () => Promise.resolve(blob) } as Response)
      .mockResolvedValueOnce({ ok: false, status: 404 } as Response);
    vi.stubGlobal('fetch', fetchMock);
    const client = new WorkOrchestratorClient(authService);

    expect(await client.getReviewImageUrl('run-1', 'S1', 'golden')).toBe('blob:img');
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(fetchMock.mock.calls[0][0]).toContain('reviews/image?run_id=run-1&sample_id=S1&kind=golden');
    expect(await client.getReviewImageUrl('run-1', 'S1', 'defect')).toBeNull();
  });

  it('puts the operator decision to the decision route', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sample_id: 'S1', final_result: 'missing part' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    await new WorkOrchestratorClient(authService).saveReviewDecision({
      run_id: 'run-1',
      sample_id: 'S1',
      selected_source: 'AI',
      final_result: 'missing part',
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/orchestrator/reviews/decision');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toMatchObject({ selected_source: 'AI', final_result: 'missing part' });
  });

  it('surfaces the error detail when the review cases cannot be loaded', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: 'explainability review agent is disabled' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    await expect(new WorkOrchestratorClient(authService).getReviewCases('run-1')).rejects.toThrow(
      'explainability review agent is disabled',
    );
  });

  it('throws the error detail when either monitoring route fails', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: 'no resolvable model for sample(s): S2' }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      new WorkOrchestratorClient(authService).flagForRetraining({
        tickets: [{ sample: SAMPLE, reason: 'x' }],
      }),
    ).rejects.toThrow('no resolvable model for sample(s): S2');
  });
});
