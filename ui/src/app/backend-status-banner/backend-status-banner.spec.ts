import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { BackendStatusService } from '../backend-status.service';
import { BackendStatusBanner } from './backend-status-banner';

describe('BackendStatusBanner', () => {
  const status = signal<'unknown' | 'online' | 'offline'>('online');
  const checking = signal(false);
  const checkNow = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    status.set('online');
    checking.set(false);
    checkNow.mockClear();
    TestBed.configureTestingModule({
      imports: [BackendStatusBanner],
      providers: [{ provide: BackendStatusService, useValue: { status, checking, checkNow } }],
    });
  });

  it('renders nothing while the backend is online', () => {
    const fixture = TestBed.createComponent(BackendStatusBanner);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent?.trim()).toBe('');
  });

  it('shows the outage message when the backend is offline', () => {
    status.set('offline');
    const fixture = TestBed.createComponent(BackendStatusBanner);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[role="alert"]')).toBeTruthy();
    expect(el.textContent).toContain('check back in a few minutes');
  });

  it('re-checks when the retry button is clicked', () => {
    status.set('offline');
    const fixture = TestBed.createComponent(BackendStatusBanner);
    fixture.detectChanges();

    fixture.nativeElement.querySelector('button').click();
    expect(checkNow).toHaveBeenCalledTimes(1);
  });

  it('disables the retry button while a check is in flight', () => {
    status.set('offline');
    checking.set(true);
    const fixture = TestBed.createComponent(BackendStatusBanner);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.textContent?.trim()).toBe('Checking…');
  });
});
