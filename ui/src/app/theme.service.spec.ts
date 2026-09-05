import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ThemeService } from './theme.service';

function mockMatchMedia(prefersDark: boolean): void {
  // jsdom doesn't implement matchMedia, so assign it directly rather than spying on it.
  window.matchMedia = vi.fn().mockReturnValue({ matches: prefersDark } as MediaQueryList);
}

describe('ThemeService', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    mockMatchMedia(false);
  });

  it('defaults to the system preference when nothing is stored', () => {
    mockMatchMedia(true);
    const service = TestBed.inject(ThemeService);
    expect(service.theme()).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('defaults to light when the system has no preference for dark', () => {
    mockMatchMedia(false);
    const service = TestBed.inject(ThemeService);
    expect(service.theme()).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('prefers a stored theme over the system preference', () => {
    localStorage.setItem('sentinel-chat.theme', 'dark');
    mockMatchMedia(false);
    const service = TestBed.inject(ThemeService);
    expect(service.theme()).toBe('dark');
  });

  it('toggles the theme, updates the html class, and persists the choice', () => {
    const service = TestBed.inject(ThemeService);
    const initial = service.theme();

    service.toggle();

    expect(service.theme()).not.toBe(initial);
    expect(document.documentElement.classList.contains('dark')).toBe(service.theme() === 'dark');
    expect(localStorage.getItem('sentinel-chat.theme')).toBe(service.theme());
  });
});
