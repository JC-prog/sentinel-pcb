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

  it('says chat uses OpenAI through the server key, with no provider choice to make', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('OpenAI');
    expect(compiled.textContent).toContain("Uses the server's configured API key");
    expect(compiled.textContent).not.toContain('Ollama');
  });

  it('closes when the close button is clicked', () => {
    settingsService.open();
    fixture.detectChanges();

    const closeButton = fixture.nativeElement.querySelector('button[aria-label="Close settings"]');
    closeButton.click();

    expect(settingsService.isOpen()).toBe(false);
  });
});
