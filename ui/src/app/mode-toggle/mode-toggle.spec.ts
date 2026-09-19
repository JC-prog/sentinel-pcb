import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { ModeToggle } from './mode-toggle';

describe('ModeToggle', () => {
  let fixture: ComponentFixture<ModeToggle>;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ModeToggle],
      providers: [provideRouter([{ path: 'work', children: [] }])],
    }).compileComponents();

    fixture = TestBed.createComponent(ModeToggle);
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it('renders both Chat and Work tabs', () => {
    const labels = Array.from(fixture.nativeElement.querySelectorAll('a')).map(
      (el) => (el as HTMLElement).textContent?.trim(),
    );
    expect(labels).toEqual(['Chat', 'Work']);
  });

  it('marks Chat as selected on the chat route', () => {
    const [chatTab, workTab] = fixture.nativeElement.querySelectorAll('a');
    expect(chatTab.getAttribute('aria-selected')).toBe('true');
    expect(workTab.getAttribute('aria-selected')).toBe('false');
  });

  it('marks Work as selected after navigating to /work', async () => {
    await router.navigateByUrl('/work');
    fixture.detectChanges();

    const [chatTab, workTab] = fixture.nativeElement.querySelectorAll('a');
    expect(workTab.getAttribute('aria-selected')).toBe('true');
    expect(chatTab.getAttribute('aria-selected')).toBe('false');
  });
});
