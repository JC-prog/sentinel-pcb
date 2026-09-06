import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { Sidebar } from './sidebar';
import { AuthService, AuthUser } from '../auth.service';
import { CHAT_RESPONDER } from '../chat-responder';
import { ChatService } from '../chat.service';
import { SettingsService } from '../settings.service';

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
  departmentShift: 'QA Day Shift',
  role: 'qa',
};

describe('Sidebar', () => {
  let component: Sidebar;
  let fixture: ComponentFixture<Sidebar>;
  let chatService: ChatService;

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [Sidebar],
      providers: [
        provideRouter([]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of('mock reply') } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Sidebar);
    component = fixture.componentInstance;
    chatService = TestBed.inject(ChatService);
    fixture.detectChanges();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('shows an empty state when there are no conversations', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('No conversations yet.');
  });

  it('lists conversations from the chat service, most recently updated first', () => {
    chatService.send(null, 'first chat', []);
    chatService.send(null, 'second chat', []);
    fixture.detectChanges();

    const titles = Array.from(fixture.nativeElement.querySelectorAll('nav a span')).map(
      (el) => (el as HTMLElement).textContent,
    );
    expect(titles).toEqual(['second chat', 'first chat']);
  });

  it('deletes a conversation without navigating into it', () => {
    const id = chatService.send(null, 'to delete', []);
    fixture.detectChanges();

    const deleteButton = fixture.nativeElement.querySelector('button[aria-label="Delete conversation"]');
    deleteButton.click();
    fixture.detectChanges();

    expect(chatService.get(id)()).toBeUndefined();
  });

  it('toggles the theme and updates the button label', () => {
    const themeButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find((btn) =>
      (btn as HTMLElement).textContent?.match(/(Light|Dark) mode/),
    ) as HTMLElement;
    const initialLabel = themeButton.textContent;

    themeButton.click();
    fixture.detectChanges();

    expect(themeButton.textContent).not.toBe(initialLabel);
  });

  it('opens settings when the settings button is clicked', () => {
    const settingsService = TestBed.inject(SettingsService);
    const settingsButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find(
      (btn) => (btn as HTMLElement).textContent?.includes('Settings'),
    ) as HTMLElement;

    settingsButton.click();

    expect(settingsService.isOpen()).toBe(true);
  });

  it('does not show user info when not logged in', () => {
    expect(fixture.nativeElement.textContent).not.toContain('Log out');
  });

  it('shows the logged-in user and logs out on click', () => {
    // Setting currentUser triggers ChatService's per-user hydration (see chat.service.ts) -
    // stub fetch so that's a deterministic no-op instead of a real network call.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false } as Response));

    const authService = TestBed.inject(AuthService);
    authService.currentUser.set(USER);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('jane-qa');
    expect(fixture.nativeElement.textContent).toContain('qa');

    const logoutSpy = vi.spyOn(authService, 'logout').mockResolvedValue(undefined);
    const logoutButton = Array.from(fixture.nativeElement.querySelectorAll('button')).find(
      (btn) => (btn as HTMLElement).textContent?.includes('Log out'),
    ) as HTMLElement;
    logoutButton.click();

    expect(logoutSpy).toHaveBeenCalled();
  });
});
