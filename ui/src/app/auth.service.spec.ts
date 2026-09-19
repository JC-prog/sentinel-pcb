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

  it('fetchCurrentUser sets null (not left undefined) when the backend is unreachable', async () => {
    // A thrown fetch (e.g. the backend still starting up on a fresh setup) must resolve
    // currentUser one way or the other - authGuard awaits this to decide whether to redirect to
    // /login, and leaving it undefined (or letting the rejection propagate) makes the guard's
    // promise reject instead of producing a redirect, so navigation just silently cancels.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(service.fetchCurrentUser()).resolves.toBeUndefined();
    expect(service.currentUser()).toBeNull();
  });

  it('register throws with the server message on a plain-string conflict error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'username already registered' }, false, 409)),
    );

    await expect(
      service.register({
        username: 'jane-qa',
        email: 'jane@example.com',
        password: 'correct-horse-battery-staple',
        employeeId: 'EMP-042',
        departmentShift: 'QA Day Shift',
        role: 'qa',
      }),
    ).rejects.toThrow('username already registered');
  });

  it('register throws a readable message (not "[object Object]") on a validation error', async () => {
    // FastAPI/Pydantic's own 422 shape: detail is a list of {loc, msg, type}, not a string - see
    // formatErrorDetail's docstring for why this needs special handling.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            detail: [
              { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' },
            ],
          },
          false,
          422,
        ),
      ),
    );

    await expect(
      service.register({
        username: 'jane-qa',
        email: 'not-an-email',
        password: 'correct-horse-battery-staple',
        employeeId: 'EMP-042',
        departmentShift: 'QA Day Shift',
        role: 'qa',
      }),
    ).rejects.toThrow('value is not a valid email address');
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
