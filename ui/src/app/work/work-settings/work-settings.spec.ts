import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkSettingsService } from '../work-settings.service';
import { WorkSettings } from './work-settings';

describe('WorkSettings', () => {
  let fixture: ComponentFixture<WorkSettings>;
  let settings: WorkSettingsService;

  const el = (testId: string) =>
    fixture.nativeElement.querySelector(`[data-testid="${testId}"]`) as HTMLInputElement;

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({ imports: [WorkSettings] }).compileComponents();
    settings = TestBed.inject(WorkSettingsService);
    settings.open();
    fixture = TestBed.createComponent(WorkSettings);
    fixture.detectChanges();
  });

  it('writes edits through to the settings service', async () => {
    await fixture.whenStable();
    const input = el('defect-threshold');
    input.value = '0.55';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    expect(settings.defectThreshold()).toBe(0.55);
  });

  it('disables the model and fallback controls while the LLM planner is off', async () => {
    await fixture.whenStable();
    expect(el('llm-model').disabled).toBe(true);
    expect(el('llm-fallback').disabled).toBe(true);

    settings.setUseLlm(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(el('llm-model').disabled).toBe(false);
    expect(el('llm-fallback').disabled).toBe(false);
  });

  it('closes on Escape', () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(settings.isOpen()).toBe(false);
  });

  it('shows the key status only once it is known', () => {
    expect(fixture.nativeElement.textContent).not.toContain('OPENAI_API_KEY');

    fixture.componentRef.setInput('llmConfigured', true);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('OPENAI_API_KEY detected');
  });
});
