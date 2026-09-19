import { Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { environment } from '../environments/environment';

export type UserRole = 'qa' | 'admin';

export interface AuthUser {
  id: string;
  username: string;
  email: string;
  employeeId: string;
  departmentShift: string;
  role: UserRole;
}

export interface RegisterInput {
  username: string;
  email: string;
  password: string;
  employeeId: string;
  departmentShift: string;
  role: UserRole;
}

interface UserResponseBody {
  id: string;
  username: string;
  email: string;
  employee_id: string;
  department_shift: string;
  role: string;
}

function toAuthUser(body: UserResponseBody): AuthUser {
  return {
    id: body.id,
    username: body.username,
    email: body.email,
    employeeId: body.employee_id,
    departmentShift: body.department_shift,
    role: body.role as UserRole,
  };
}

/**
 * `detail` isn't always a plain string: FastAPI/Pydantic request-validation failures (a bad
 * email format, a field that's too short, ...) send `detail` as a list of
 * `{loc, msg, type}` objects instead - our own HTTPException(detail="...") calls (wrong
 * password, username already registered, ...) are the plain-string case. Left untreated, the
 * array falls through as truthy and gets passed straight to `new Error(...)`, which coerces it
 * to the literal string "[object Object]" (or a comma-joined run of them) - not what a validation
 * error should show the user.
 */
function formatErrorDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item),
      )
      .join('; ');
  }
  return null;
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body: { detail?: unknown } = await response.json();
    return formatErrorDetail(body.detail) ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  /** undefined = not checked yet, null = checked and not logged in. */
  readonly currentUser = signal<AuthUser | null | undefined>(undefined);

  private refreshInFlight: Promise<boolean> | null = null;

  constructor(private readonly router: Router) {}

  /**
   * Failures here must never leave currentUser stuck at `undefined` - authGuard awaits this
   * exact call to decide whether to redirect to /login, and an unhandled rejection propagating
   * out of an async CanActivateFn makes the router silently cancel navigation instead of
   * redirecting, so the page can appear to just hang. A thrown fetch (backend unreachable - e.g.
   * still starting up on a fresh `docker compose up`, or a dropped connection) is treated the
   * same as "not logged in": there's no session to trust either way.
   */
  async fetchCurrentUser(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBaseUrl}/api/auth/me`, {
        credentials: 'include',
      });
      this.currentUser.set(response.ok ? toAuthUser(await response.json()) : null);
    } catch {
      this.currentUser.set(null);
    }
  }

  async register(input: RegisterInput): Promise<void> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        username: input.username,
        email: input.email,
        password: input.password,
        employee_id: input.employeeId,
        department_shift: input.departmentShift,
        role: input.role,
      }),
    });
    if (!response.ok) {
      throw new Error(await extractErrorMessage(response));
    }
    this.currentUser.set(toAuthUser(await response.json()));
  }

  async login(username: string, password: string): Promise<void> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      throw new Error(await extractErrorMessage(response));
    }
    this.currentUser.set(toAuthUser(await response.json()));
  }

  async logout(): Promise<void> {
    await fetch(`${environment.apiBaseUrl}/api/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
    this.currentUser.set(null);
    await this.router.navigateByUrl('/login');
  }

  /**
   * Wraps fetch() with a one-time silent refresh on a 401: the access token cookie is short-
   * lived (15 min) by design (app/settings.py), so without this every request would eventually
   * fail once it expires mid-session instead of quietly renewing. Concurrent 401s share a single
   * in-flight refresh call rather than each racing to refresh separately.
   */
  async fetchWithAuth(input: string, init: RequestInit = {}): Promise<Response> {
    const response = await fetch(input, { ...init, credentials: 'include' });
    if (response.status !== 401) {
      return response;
    }

    if (!this.refreshInFlight) {
      this.refreshInFlight = this.tryRefresh().finally(() => {
        this.refreshInFlight = null;
      });
    }
    const refreshed = await this.refreshInFlight;
    if (!refreshed) {
      this.currentUser.set(null);
      await this.router.navigateByUrl('/login');
      return response;
    }
    return fetch(input, { ...init, credentials: 'include' });
  }

  private async tryRefresh(): Promise<boolean> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!response.ok) {
      return false;
    }
    this.currentUser.set(toAuthUser(await response.json()));
    return true;
  }
}
