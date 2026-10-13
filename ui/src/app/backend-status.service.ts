import { Injectable, OnDestroy, signal } from '@angular/core';
import { environment } from '../environments/environment';

export type BackendStatus = 'unknown' | 'online' | 'offline';

/** How often to re-check /health while the backend looks healthy. */
const HEALTHY_INTERVAL_MS = 30_000;
/** Faster cadence once it looks down, so the banner clears quickly on recovery. */
const UNHEALTHY_INTERVAL_MS = 10_000;
/** A single /health call is given this long before it counts as a failure. */
const PING_TIMEOUT_MS = 5_000;
/** Consecutive failed checks before we tell the user the backend is down. Keeps one dropped
 * request (a redeploy blip, a flaky connection) from flashing the banner at everyone. */
const FAILURE_THRESHOLD = 2;

/**
 * Background poll of the API's `/health` endpoint so the UI can tell users when the backend is
 * unreachable instead of leaving them with silently failing requests. `status` starts 'unknown'
 * and settles to 'online' / 'offline'; the banner (ui/src/app/backend-status-banner) only shows
 * for 'offline'. Recovery is automatic - the next successful check flips it back to 'online'.
 */
@Injectable({ providedIn: 'root' })
export class BackendStatusService implements OnDestroy {
  private readonly _status = signal<BackendStatus>('unknown');
  private readonly _checking = signal(false);

  /** 'unknown' until the first check resolves, then 'online' / 'offline'. */
  readonly status = this._status.asReadonly();
  /** True while a /health request is in flight - drives the banner's retry button. */
  readonly checking = this._checking.asReadonly();

  private failures = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor() {
    void this.runCheck();
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.wake);
      document.addEventListener('visibilitychange', this.wake);
    }
  }

  ngOnDestroy(): void {
    this.stopped = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this.wake);
      document.removeEventListener('visibilitychange', this.wake);
    }
  }

  /** Network came back or the tab was refocused - re-check right away rather than waiting out
   * the rest of the polling interval. */
  private readonly wake = (): void => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }
    void this.checkNow();
  };

  /** Force an immediate re-check (the banner's "Retry now" button). No-op if one is already
   * running. */
  async checkNow(): Promise<void> {
    if (this._checking()) {
      return;
    }
    await this.runCheck();
  }

  private async runCheck(): Promise<void> {
    this._checking.set(true);
    const healthy = await this.ping();
    this._checking.set(false);
    if (this.stopped) {
      return;
    }

    if (healthy) {
      this.failures = 0;
      this._status.set('online');
    } else {
      this.failures += 1;
      if (this.failures >= FAILURE_THRESHOLD) {
        this._status.set('offline');
      }
    }
    this.schedule();
  }

  private schedule(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
    }
    const delay = this._status() === 'offline' ? UNHEALTHY_INTERVAL_MS : HEALTHY_INTERVAL_MS;
    this.timer = setTimeout(() => void this.runCheck(), delay);
  }

  private async ping(): Promise<boolean> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS);
    try {
      const response = await fetch(`${environment.apiBaseUrl}/health`, {
        signal: controller.signal,
        cache: 'no-store',
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }
}
