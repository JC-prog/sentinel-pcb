import { TestBed } from '@angular/core/testing';
import { ThemeToggle } from './theme-toggle';
import { ThemeService } from '../theme.service';

describe('ThemeToggle', () => {
  beforeEach(async () => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    await TestBed.configureTestingModule({ imports: [ThemeToggle] }).compileComponents();
  });

  it('should create', () => {
    const fixture = TestBed.createComponent(ThemeToggle);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('toggles the theme and reflects the state via aria-checked when clicked', () => {
    const fixture = TestBed.createComponent(ThemeToggle);
    fixture.detectChanges();
    const themeService = TestBed.inject(ThemeService);
    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    const initialTheme = themeService.theme();
    const initialChecked = button.getAttribute('aria-checked');

    button.click();
    fixture.detectChanges();

    expect(themeService.theme()).not.toBe(initialTheme);
    expect(button.getAttribute('aria-checked')).not.toBe(initialChecked);
  });
});
