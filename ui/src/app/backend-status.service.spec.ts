import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { BackendStatusService } from './backend-status.service';

const HEALTHY_INTERVAL_MS = 30_000;
const UNHEALTHY_INTERVAL_MS = 10_000;

describe('BackendStatusService', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  const ok = (): Promise<Response> => Promise.resolve({ ok: true } as Response);
  const unreachable = (): Promise<Response> => Promise.reject(new Error('network down'));

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('starts unknown and becomes online after a healthy check', async () => {
    fetchMock.mockImplementation(ok);
    const service = TestBed.inject(BackendStatusService);

    expect(service.status()).toBe('unknown');
    await vi.advanceTimersByTimeAsync(0);

    expect(service.status()).toBe('online');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/health'),
      expect.objectContaining({ cache: 'no-store' }),
    );
  });

  it('stays online after a single failed check (below the threshold)', async () => {
    fetchMock.mockImplementationOnce(ok);
    const service = TestBed.inject(BackendStatusService);
    await vi.advanceTimersByTimeAsync(0);
    expect(service.status()).toBe('online');

    fetchMock.mockImplementation(unreachable);
    await vi.advanceTimersByTimeAsync(HEALTHY_INTERVAL_MS);

    expect(service.status()).toBe('online');
  });

  it('goes offline only after consecutive failed checks', async () => {
    fetchMock.mockImplementation(unreachable);
    const service = TestBed.inject(BackendStatusService);

    await vi.advanceTimersByTimeAsync(0);
    expect(service.status()).toBe('unknown');

    await vi.advanceTimersByTimeAsync(HEALTHY_INTERVAL_MS);
    expect(service.status()).toBe('offline');
  });

  it('recovers to online once /health responds again', async () => {
    fetchMock.mockImplementation(unreachable);
    const service = TestBed.inject(BackendStatusService);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(HEALTHY_INTERVAL_MS);
    expect(service.status()).toBe('offline');

    fetchMock.mockImplementation(ok);
    await vi.advanceTimersByTimeAsync(UNHEALTHY_INTERVAL_MS);

    expect(service.status()).toBe('online');
  });

  it('checkNow() forces an immediate re-check', async () => {
    fetchMock.mockImplementation(unreachable);
    const service = TestBed.inject(BackendStatusService);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(HEALTHY_INTERVAL_MS);
    expect(service.status()).toBe('offline');

    fetchMock.mockImplementation(ok);
    await service.checkNow();

    expect(service.status()).toBe('online');
  });

  it('treats a slow /health call as a failure once it times out', async () => {
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new Error('aborted')));
        }),
    );
    const service = TestBed.inject(BackendStatusService);

    await vi.advanceTimersByTimeAsync(5_000); // first ping aborts
    await vi.advanceTimersByTimeAsync(HEALTHY_INTERVAL_MS + 5_000); // second ping aborts

    expect(service.status()).toBe('offline');
  });
});
