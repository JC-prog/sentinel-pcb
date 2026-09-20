import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Settings } from './settings';
import { SettingsService } from '../settings.service';

describe('Settings', () => {
  let component: Settings;
  let fixture: ComponentFixture<Settings>;
  let settingsService: SettingsService;

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [Settings],
    }).compileComponents();

    fixture = TestBed.createComponent(Settings);
    component = fixture.componentInstance;
    settingsService = TestBed.inject(SettingsService);
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('does not show the server-key note for the ollama provider', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).not.toContain("Uses the server's configured API key");
  });

  it('shows the server-key note once openai is selected', () => {
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLElement[];
    const openaiButton = buttons.find((btn) => btn.textContent?.includes('OpenAI'));
    openaiButton!.click();
    fixture.detectChanges();

    expect(settingsService.provider()).toBe('openai');
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain("Uses the server's configured API key");
  });

  it('closes when the close button is clicked', () => {
    settingsService.open();
    fixture.detectChanges();

    const closeButton = fixture.nativeElement.querySelector('button[aria-label="Close settings"]');
    closeButton.click();

    expect(settingsService.isOpen()).toBe(false);
  });
});
