import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { AuthService, AuthUser } from './auth.service';
import { ChatResponder, CHAT_RESPONDER } from './chat-responder';
import { ChatService } from './chat.service';

function createFile(name = 'board.png', type = 'image/png'): File {
  return new File(['fake-bytes'], name, { type });
}

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
  departmentShift: 'QA Day Shift',
  role: 'qa',
};

/** ChatService only ever calls currentUser() and fetchWithAuth() - a minimal stand-in avoids
 * pulling in AuthService's own Router dependency, same reasoning as
 * http-chat-responder.spec.ts's fakeAuthService(). fetchWithAuth defaults to a failed response
 * so server hydration silently no-ops and tests exercise the local/optimistic behavior. */
function fakeAuthService(user: AuthUser | null = USER): AuthService {
  return {
    currentUser: () => user,
    fetchWithAuth: vi.fn().mockResolvedValue({ ok: false } as Response),
  } as unknown as AuthService;
}

function createChatService(responder: ChatResponder, authService: AuthService = fakeAuthService()): ChatService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: CHAT_RESPONDER, useValue: responder },
      { provide: AuthService, useValue: authService },
    ],
  });
  const service = TestBed.inject(ChatService);
  // ChatService's per-user storage key is set reactively (see chat.service.ts's constructor
  // effect) - flush it now so tests can rely on the right key being active immediately.
  TestBed.tick();
  return service;
}

describe('ChatService', () => {
  let responder: ChatResponder;
  let service: ChatService;

  beforeEach(() => {
    localStorage.clear();
    responder = { respond: vi.fn().mockReturnValue(of('mock reply')) };
    service = createChatService(responder);
  });

  it('starts with no conversations', () => {
    expect(service.list()()).toEqual([]);
  });

  it('creates a new conversation on first send and derives a title from the message', () => {
    const id = service.send(null, 'Where are my PCB defects?', []);

    const conversations = service.list()();
    expect(conversations.length).toBe(1);
    expect(conversations[0].id).toBe(id);
    expect(conversations[0].title).toBe('Where are my PCB defects?');
  });

  it('appends the assistant reply from the responder after sending', () => {
    const id = service.send(null, 'hello', []);

    const conversation = service.get(id)();
    expect(conversation?.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(conversation?.messages[1].content).toBe('mock reply');
  });

  it('sets isLoading while awaiting a reply and clears it once resolved', () => {
    const chunks = new Subject<string>();
    const slowService = createChatService({ respond: () => chunks.asObservable() });

    const id = slowService.send(null, 'hi', []);
    expect(slowService.isLoading(id)()).toBe(true);

    chunks.next('done');
    chunks.complete();
    expect(slowService.isLoading(id)()).toBe(false);
  });

  it('accumulates streamed chunks into a single assistant message as they arrive', () => {
    const chunks = new Subject<string>();
    const streamingService = createChatService({ respond: () => chunks.asObservable() });

    const id = streamingService.send(null, 'hi', []);
    chunks.next('Hel');
    expect(streamingService.get(id)()?.messages[1].content).toBe('Hel');

    chunks.next('lo');
    chunks.complete();
    expect(streamingService.get(id)()?.messages[1].content).toBe('Hello');
    expect(streamingService.get(id)()?.messages.length).toBe(2);
  });

  it('appends a fallback message and clears loading when the responder errors', () => {
    const erroringService = createChatService({ respond: () => throwError(() => new Error('boom')) });

    const id = erroringService.send(null, 'hi', []);

    const conversation = erroringService.get(id)();
    expect(conversation?.messages[1].role).toBe('assistant');
    expect(conversation?.messages[1].content).toContain('went wrong');
    expect(erroringService.isLoading(id)()).toBe(false);
  });

  it('creates object URLs for attached images on the user message', () => {
    const id = service.send(null, 'see attached', [createFile()]);
    const conversation = service.get(id)();
    expect(conversation?.messages[0].imageUrls?.length).toBe(1);
  });

  it('adds a follow-up message to the same conversation instead of creating a new one', () => {
    const id = service.send(null, 'first', []);
    service.send(id, 'second', []);

    const conversation = service.get(id)();
    expect(conversation?.messages.map((m) => m.content)).toEqual(['first', 'mock reply', 'second', 'mock reply']);
    expect(service.list()().length).toBe(1);
  });

  it('deletes a conversation', () => {
    const id = service.send(null, 'first', []);
    service.delete(id);
    expect(service.list()()).toEqual([]);
  });

  it('persists text history to localStorage, keyed per user, without image object URLs', () => {
    service.send(null, 'see attached', [createFile()]);

    const raw = localStorage.getItem(`sentinel-chat.conversations.${USER.id}`);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed[0].messages[0].content).toBe('see attached');
    expect(parsed[0].messages[0].imageUrls).toBeUndefined();
  });

  it('restores conversations from localStorage on a fresh load for the same user', () => {
    service.send(null, 'remembered', []);

    const restored = createChatService(responder);
    expect(restored.list()().length).toBe(1);
    expect(restored.list()()[0].title).toBe('remembered');
  });

  it('does not show one user\'s conversations to a different user on the same browser', () => {
    service.send(null, 'user one secret', []);

    const otherUser: AuthUser = { ...USER, id: 'user-2' };
    const otherUsersService = createChatService(responder, fakeAuthService(otherUser));

    expect(otherUsersService.list()()).toEqual([]);
  });
});
