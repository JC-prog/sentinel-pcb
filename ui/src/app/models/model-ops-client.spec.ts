import { vi } from 'vitest';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import { ModelOpsClient } from './model-ops-client';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** ModelOpsClient only ever calls fetchWithAuth() - a stand-in avoids AuthService's Router
 * dependency, same approach as work-orchestrator-client.spec.ts. */
const API = environment.apiBaseUrl;

function clientWith(fetchWithAuth: ReturnType<typeof vi.fn>): ModelOpsClient {
  return new ModelOpsClient({ fetchWithAuth } as unknown as AuthService);
}

describe('ModelOpsClient', () => {
  afterEach(() => vi.restoreAllMocks());

  it('GETs the overview, drift (with its window) and queue from /api/models', async () => {
    const fetchWithAuth = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = clientWith(fetchWithAuth);

    await client.overview();
    await client.drift(7);
    await client.queue();

    expect(fetchWithAuth.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
      [`${API}/api/models`, 'GET'],
      [`${API}/api/models/drift?days=7`, 'GET'],
      [`${API}/api/models/retraining/queue`, 'GET'],
    ]);
  });

  it('POSTs the job actions to the job, url-encoding its id', async () => {
    const fetchWithAuth = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = clientWith(fetchWithAuth);

    await client.job('a/b');
    await client.approveJob('j1');
    await client.submitJob('j1');
    await client.cancelJob('j1');
    await client.resolveDrift('r1');

    expect(fetchWithAuth.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
      [`${API}/api/models/retraining/jobs/a%2Fb`, 'GET'],
      [`${API}/api/models/retraining/jobs/j1/approve`, 'POST'],
      [`${API}/api/models/retraining/jobs/j1/submit`, 'POST'],
      [`${API}/api/models/retraining/jobs/j1/cancel`, 'POST'],
      [`${API}/api/models/drift/r1/resolve`, 'POST'],
    ]);
  });

  it('promotes with the version in a JSON body and rolls back with none', async () => {
    const fetchWithAuth = vi.fn().mockResolvedValue(jsonResponse({}));
    const client = clientWith(fetchWithAuth);

    await client.promote('pcb_body_defect', 'JcProg/body@v2');
    await client.rollback('pcb_body_defect');

    const [promoteCall, rollbackCall] = fetchWithAuth.mock.calls;
    expect(promoteCall[0]).toBe(`${API}/api/models/pcb_body_defect/promote`);
    expect(promoteCall[1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(promoteCall[1].body)).toEqual({ version: 'JcProg/body@v2' });
    expect(rollbackCall[0]).toBe(`${API}/api/models/pcb_body_defect/rollback`);
    expect(rollbackCall[1].body).toBeUndefined();
  });

  it("returns the response's JSON", async () => {
    const body = { inference: { configured: true, reachable: true, error: null }, models: [] };
    const client = clientWith(vi.fn().mockResolvedValue(jsonResponse(body)));

    expect(await client.overview()).toEqual(body);
  });

  it("throws the API's detail message on an error status", async () => {
    const client = clientWith(
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'admin role required' }, 403)),
    );

    await expect(client.approveJob('j1')).rejects.toThrow('admin role required');
  });

  it('joins validation errors that arrive as a list', async () => {
    const detail = [{ msg: 'field required' }, { msg: 'value too short' }];
    const client = clientWith(vi.fn().mockResolvedValue(jsonResponse({ detail }, 422)));

    await expect(client.promote('m', 'v')).rejects.toThrow('field required; value too short');
  });

  it('falls back to the status when the error body is not JSON', async () => {
    const notJson = {
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response;
    const client = clientWith(vi.fn().mockResolvedValue(notJson));

    await expect(client.queue()).rejects.toThrow('Request failed (502)');
  });
});
