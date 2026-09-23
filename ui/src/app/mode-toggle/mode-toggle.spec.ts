import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { ModeToggle } from './mode-toggle';

describe('ModeToggle', () => {
  let fixture: ComponentFixture<ModeToggle>;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ModeToggle],
      providers: [
        provideRouter([
          { path: 'work', children: [] },
          { path: 'models', children: [] },
        ]),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ModeToggle);
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  function tabs(): { label: string; selected: string | null; href: string | null }[] {
    return Array.from(fixture.nativeElement.querySelectorAll('a')).map((el) => ({
      label: (el as HTMLElement).textContent?.trim() ?? '',
      selected: (el as HTMLElement).getAttribute('aria-selected'),
      href: (el as HTMLElement).getAttribute('href'),
    }));
  }

  async function goTo(url: string): Promise<void> {
    await router.navigateByUrl(url);
    fixture.detectChanges();
  }

  it('renders the Chat, Work and Models tabs, each linking to its page', () => {
    expect(tabs().map((t) => [t.label, t.href])).toEqual([
      ['Chat', '/chat'],
      ['Work', '/work'],
      ['Models', '/models'],
    ]);
  });

  it('marks Chat as selected on the chat route', () => {
    expect(tabs().map((t) => t.selected)).toEqual(['true', 'false', 'false']);
  });

  it('marks Work as selected after navigating to /work', async () => {
    await goTo('/work');

    expect(tabs().map((t) => t.selected)).toEqual(['false', 'true', 'false']);
  });

  it('marks Models as selected after navigating to /models', async () => {
    await goTo('/models');

    expect(tabs().map((t) => t.selected)).toEqual(['false', 'false', 'true']);
  });
});
