import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from './auth.service';

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

const USER_BODY = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employee_id: 'EMP-042',
  department_shift: 'QA Day Shift',
  role: 'qa',
};

describe('AuthService', () => {
  let service: AuthService;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [provideRouter([])],
    }).compileComponents();
    service = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('starts with currentUser undefined (not yet checked)', () => {
    expect(service.currentUser()).toBeUndefined();
  });

  it('fetchCurrentUser sets the user on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(USER_BODY)));
    await service.fetchCurrentUser();
    expect(service.currentUser()?.email).toBe('jane@example.com');
    expect(service.currentUser()?.employeeId).toBe('EMP-042');
  });

  it('fetchCurrentUser sets null when not authenticated', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, false, 401)));
    await service.fetchCurrentUser();
    expect(service.currentUser()).toBeNull();
  });

  it('login sets the current user and throws with the server message on failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(USER_BODY)));
    await service.login('jane-qa', 'correct-horse-battery-staple');
    expect(service.currentUser()?.username).toBe('jane-qa');

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(jsonResponse({ detail: 'incorrect username or password' }, false, 401)),
    );
    await expect(service.login('jane-qa', 'wrong')).rejects.toThrow(
      'incorrect username or password',
    );
  });

  it('logout clears the current user and navigates to /login', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(USER_BODY)));
    await service.fetchCurrentUser();

    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    await service.logout();

    expect(service.currentUser()).toBeNull();
    expect(navigateSpy).toHaveBeenCalledWith('/login');
  });

  it('fetchWithAuth passes non-401 responses straight through', async () => {
    const okResponse = jsonResponse({ ok: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okResponse));

    const result = await service.fetchWithAuth('/api/whatever');
    expect(result).toBe(okResponse);
  });

  it('fetchWithAuth refreshes once and retries on a 401, then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, false, 401)) // original request
      .mockResolvedValueOnce(jsonResponse(USER_BODY)) // refresh call
      .mockResolvedValueOnce(jsonResponse({ ok: true })); // retried request
    vi.stubGlobal('fetch', fetchMock);

    const result = await service.fetchWithAuth('/api/protected');

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(await result.json()).toEqual({ ok: true });
    expect(service.currentUser()?.email).toBe('jane@example.com');
  });

  it('fetchWithAuth redirects to /login when the refresh itself fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, false, 401)) // original request
      .mockResolvedValueOnce(jsonResponse({}, false, 401)); // refresh call fails too
    vi.stubGlobal('fetch', fetchMock);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    await service.fetchWithAuth('/api/protected');

    expect(service.currentUser()).toBeNull();
    expect(navigateSpy).toHaveBeenCalledWith('/login');
  });
});
