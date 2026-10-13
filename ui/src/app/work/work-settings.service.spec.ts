import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { DEFAULT_THRESHOLD, WorkSettingsService } from './work-settings.service';

const KEY = 'sentinel-work.settings';

function create(): WorkSettingsService {
  TestBed.resetTestingModule();
  return TestBed.inject(WorkSettingsService);
}

describe('WorkSettingsService', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('starts with the defaults the run request used before settings existed', () => {
    const s = create();
    expect(s.featureThreshold()).toBe(DEFAULT_THRESHOLD);
    expect(s.defectThreshold()).toBe(DEFAULT_THRESHOLD);
    expect(s.useLlm()).toBe(false);
    expect(s.llmModel()).toBe('');
    expect(s.llmFallback()).toBe(true);
  });

  it('keeps a change across a reload', () => {
    const s = create();
    s.setFeatureThreshold(0.4);
    s.setUseLlm(true);
    s.setLlmModel('gpt-x');
    s.setLlmFallback(false);

    const reloaded = create();
    expect(reloaded.featureThreshold()).toBe(0.4);
    expect(reloaded.useLlm()).toBe(true);
    expect(reloaded.llmModel()).toBe('gpt-x');
    expect(reloaded.llmFallback()).toBe(false);
  });

  it('clamps thresholds to 0..1 and ignores a cleared or non-numeric input', () => {
    const s = create();
    s.setFeatureThreshold(1.5);
    expect(s.featureThreshold()).toBe(1);
    s.setFeatureThreshold(-2);
    expect(s.featureThreshold()).toBe(0);

    s.setDefectThreshold(0.9);
    s.setDefectThreshold(null);
    s.setDefectThreshold(Number.NaN);
    s.setDefectThreshold('abc');
    expect(s.defectThreshold()).toBe(0.9);
  });

  it('falls back to defaults when the stored value is corrupt', () => {
    localStorage.setItem(KEY, '{not json');
    expect(create().featureThreshold()).toBe(DEFAULT_THRESHOLD);

    localStorage.setItem(KEY, JSON.stringify({ featureThreshold: 'x', useLlm: 'yes', llmModel: 3 }));
    const s = create();
    expect(s.featureThreshold()).toBe(DEFAULT_THRESHOLD);
    expect(s.useLlm()).toBe(false);
    expect(s.llmModel()).toBe('');
  });

  it('still works when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const s = create();
    expect(() => s.setUseLlm(true)).not.toThrow();
    expect(s.useLlm()).toBe(true);
  });
});
