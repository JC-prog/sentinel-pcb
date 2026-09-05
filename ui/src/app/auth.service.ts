import { Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { environment } from '../environments/environment';

export type UserRole = 'qa' | 'operator' | 'admin';
export type RegistrableRole = 'qa' | 'operator';

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  employeeId: string;
  departmentShift: string;
  role: UserRole;
}

export interface RegisterInput {
  name: string;
  email: string;
  password: string;
  employeeId: string;
  departmentShift: string;
  role: RegistrableRole;
}

interface UserResponseBody {
  id: string;
  name: string;
  email: string;
  employee_id: string;
  department_shift: string;
  role: string;
}

function toAuthUser(body: UserResponseBody): AuthUser {
  return {
    id: body.id,
    name: body.name,
    email: body.email,
    employeeId: body.employee_id,
    departmentShift: body.department_shift,
    role: body.role as UserRole,
  };
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body: { detail?: string } = await response.json();
    return body.detail ?? `Request failed (${response.status})`;
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

  async fetchCurrentUser(): Promise<void> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/me`, {
      credentials: 'include',
    });
    this.currentUser.set(response.ok ? toAuthUser(await response.json()) : null);
  }

  async register(input: RegisterInput): Promise<void> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        name: input.name,
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

  async login(email: string, password: string): Promise<void> {
    const response = await fetch(`${environment.apiBaseUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
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
