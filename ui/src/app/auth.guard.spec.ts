import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { AuthService, AuthUser } from './auth.service';
import { authGuard } from './auth.guard';

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
  departmentShift: 'QA Day Shift',
  role: 'qa',
};

describe('authGuard', () => {
  let authService: AuthService;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [provideRouter([])],
    }).compileComponents();
    authService = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
  });

  function runGuard(): Promise<unknown> {
    return TestBed.runInInjectionContext(() =>
      Promise.resolve(authGuard({} as never, {} as never)),
    );
  }

  it('allows activation when already known to be logged in', async () => {
    authService.currentUser.set(USER);
    const fetchSpy = vi.spyOn(authService, 'fetchCurrentUser');

    expect(await runGuard()).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('checks the session first when not yet known, then allows activation', async () => {
    vi.spyOn(authService, 'fetchCurrentUser').mockImplementation(async () => {
      authService.currentUser.set(USER);
    });

    expect(await runGuard()).toBe(true);
  });

  it('redirects to /login when not authenticated', async () => {
    vi.spyOn(authService, 'fetchCurrentUser').mockImplementation(async () => {
      authService.currentUser.set(null);
    });

    const result = await runGuard();
    expect(router.serializeUrl(result as never)).toBe('/login');
  });
});
