import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Component } from '@angular/core';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { Sidebar } from './sidebar';
import { AuthService, AuthUser } from '../../auth.service';
import { CHAT_RESPONDER } from '../chat-responder';
import { ChatService } from '../chat.service';
import { SettingsService } from '../settings.service';
import { WorkService } from '../../work/work.service';

@Component({ template: '', selector: 'app-test-stub' })
class StubComponent {}

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
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
        provideRouter([
          { path: '', component: StubComponent },
          { path: 'work', component: StubComponent },
          { path: 'models', component: StubComponent },
          { path: 'cases', component: StubComponent },
        ]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'mock reply' }) } },
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
    expect(chatService.get(id)()).toBeDefined(); // asks first

    fixture.nativeElement.querySelector('button[aria-label="Confirm delete"]').click();
    fixture.detectChanges();

    expect(chatService.get(id)()).toBeUndefined();
  });

  it('keeps the conversation when the delete is cancelled', () => {
    const id = chatService.send(null, 'keep me', []);
    fixture.detectChanges();

    fixture.nativeElement.querySelector('button[aria-label="Delete conversation"]').click();
    fixture.detectChanges();
    const cancel = Array.from(fixture.nativeElement.querySelectorAll('button')).find(
      (btn) => (btn as HTMLElement).textContent?.trim() === 'Cancel',
    ) as HTMLElement;
    cancel.click();
    fixture.detectChanges();

    expect(chatService.get(id)()).toBeDefined();
    expect(fixture.nativeElement.querySelector('button[aria-label="Delete conversation"]')).toBeTruthy();
  });

  it('renames a conversation from the sidebar', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true } as Response));
    const id = chatService.send(null, 'untitled idea', []);
    fixture.detectChanges();

    fixture.nativeElement.querySelector('button[aria-label="Rename conversation"]').click();
    fixture.detectChanges();
    const input = fixture.nativeElement.querySelector('input[aria-label="Conversation title"]') as HTMLInputElement;
    input.value = '  Board A7   reflow ';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    fixture.detectChanges();

    expect(chatService.get(id)()?.title).toBe('Board A7 reflow');
  });

  it('filters conversations by what you type in the search box', () => {
    chatService.send(null, 'solder bridge on U7', []);
    chatService.send(null, 'capacitor polarity', []);
    fixture.detectChanges();

    const search = fixture.nativeElement.querySelector('input[type="search"]') as HTMLInputElement;
    search.value = 'solder';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    const titles = Array.from(fixture.nativeElement.querySelectorAll('nav a span')).map(
      (el) => (el as HTMLElement).textContent,
    );
    expect(titles).toEqual(['solder bridge on U7']);
  });

  it('says so when nothing matches the search', () => {
    chatService.send(null, 'solder bridge on U7', []);
    fixture.detectChanges();

    const search = fixture.nativeElement.querySelector('input[type="search"]') as HTMLInputElement;
    search.value = 'zzz';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('No chats match your search.');
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

  it('hides "New chat" and conversation history on the Work route (it lists runs), keeps Settings/logout', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false } as Response));

    const router = TestBed.inject(Router);
    await router.navigateByUrl('/work');
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('New chat');
    // no conversation list: the Work route lists its runs instead
    expect(fixture.nativeElement.querySelector('nav[aria-label="Conversations"]')).toBeFalsy();

    const authService = TestBed.inject(AuthService);
    authService.currentUser.set(USER);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('jane-qa');
    expect(fixture.nativeElement.textContent).toContain('Log out');
  });

  it('hides "New chat" and conversation history on the Models route too', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/models');
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('New chat');
    expect(fixture.nativeElement.querySelector('nav')).toBeFalsy();
  });

  it('keeps the chat list hidden on the Cases route, also with a case open', async () => {
    await TestBed.inject(Router).navigateByUrl('/cases?case=CASE-000012');
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('New chat');
    expect(fixture.nativeElement.querySelector('nav[aria-label="Conversations"]')).toBeFalsy();
  });

  it('shows "New chat" and conversation history again after navigating back to chat', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/work');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).not.toContain('New chat');

    await router.navigateByUrl('/');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('New chat');
  });

  describe('run history on the Work route', () => {
    const RUN = {
      run_id: 'abcdef1234567890',
      saved_at_utc: '2026-10-12T14:05:00+00:00',
      status: 'REVIEW_REQUIRED',
      input_samples: 12,
      review_required: 5,
      decided: 2,
    };

    function stubRuns(runs: unknown[], available = true): void {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({ ok: true, json: async () => ({ runs, available }) } as Response),
      );
      const auth = TestBed.inject(AuthService);
      vi.spyOn(auth, 'fetchWithAuth').mockResolvedValue({
        ok: true,
        json: async () => ({ runs, available }),
      } as Response);
    }

    async function openWork(): Promise<void> {
      await TestBed.inject(Router).navigateByUrl('/work');
      fixture.detectChanges();
      // the history is fetched from an effect; let that promise settle, then render it
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();
    }

    it('lists stored runs instead of conversations', async () => {
      stubRuns([RUN]);

      await openWork();

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Runs');
      expect(text).toContain('abcdef12');
      expect(text).toContain('12 samples · 5 flagged · 2 decided');
      expect(text).not.toContain('New chat');
    });

    it('says there are no runs yet', async () => {
      stubRuns([]);

      await openWork();

      expect(fixture.nativeElement.textContent).toContain('No runs yet');
    });

    it('says when history is unavailable rather than showing an empty list', async () => {
      stubRuns([], false);

      await openWork();

      expect(fixture.nativeElement.textContent).toContain('Run history is unavailable');
    });

    it('opens a run when it is clicked', async () => {
      stubRuns([RUN]);
      const work = TestBed.inject(WorkService);
      const open = vi.spyOn(work, 'openRun').mockResolvedValue();
      await openWork();

      (fixture.nativeElement.querySelector('nav[aria-label="Runs"] button') as HTMLElement).click();

      expect(open).toHaveBeenCalledWith('abcdef1234567890');
    });
  });
});
