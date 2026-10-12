import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkSettingsService } from '../work-settings.service';
import { WorkSettings } from './work-settings';

describe('WorkSettings', () => {
  let fixture: ComponentFixture<WorkSettings>;
  let settings: WorkSettingsService;

  const el = (testId: string) =>
    fixture.nativeElement.querySelector(`[data-testid="${testId}"]`) as HTMLInputElement;

  async function type(testId: string, value: string): Promise<void> {
    const input = el(testId);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    await fixture.whenStable();
  }

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({ imports: [WorkSettings] }).compileComponents();
    settings = TestBed.inject(WorkSettingsService);
    settings.open();
    fixture = TestBed.createComponent(WorkSettings);
    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('holds edits as a draft until Save is pressed', async () => {
    await type('defect-threshold', '0.55');
    expect(settings.defectThreshold()).toBe(0.7);

    el('settings-save').click();

    expect(settings.defectThreshold()).toBe(0.55);
    expect(settings.isOpen()).toBe(false);
  });

  it('discards the draft on Cancel', async () => {
    await type('defect-threshold', '0.55');

    el('settings-cancel').click();

    expect(settings.defectThreshold()).toBe(0.7);
    expect(settings.isOpen()).toBe(false);
  });

  it('discards the draft on Escape', async () => {
    await type('feature-threshold', '0.2');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(settings.featureThreshold()).toBe(0.7);
    expect(settings.isOpen()).toBe(false);
  });

  it('will not save a threshold outside 0 to 1', async () => {
    await type('feature-threshold', '1.5');

    expect(el('settings-save').disabled).toBe(true);
    expect(el('threshold-error')).not.toBeNull();
  });

  it('disables the model and fallback controls while the LLM planner is off', async () => {
    expect(el('llm-model').disabled).toBe(true);
    expect(el('llm-fallback').disabled).toBe(true);

    el('use-llm').click();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(el('llm-model').disabled).toBe(false);
    expect(el('llm-fallback').disabled).toBe(false);
  });

  it('shows the key status only once it is known', () => {
    expect(fixture.nativeElement.textContent).not.toContain('OPENAI_API_KEY');

    fixture.componentRef.setInput('llmConfigured', true);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('OPENAI_API_KEY detected');
  });
});
