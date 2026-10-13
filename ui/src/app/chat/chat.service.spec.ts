import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { AuthService, AuthUser } from '../auth.service';
import {
  ChatResponder,
  ChatResponderError,
  ChatResponderEvent,
  CHAT_RESPONDER,
} from './chat-responder';
import { ChatService } from './chat.service';
import { ToolResult } from './models/chat.models';

function createFile(name = 'board.png', type = 'image/png'): File {
  return new File(['fake-bytes'], name, { type });
}

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
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
    responder = { respond: vi.fn().mockReturnValue(of({ type: 'delta', text: 'mock reply' })) };
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

  it('stop() ends a streaming reply, keeps the text so far and unsubscribes the responder', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const slowService = createChatService({ respond: () => chunks.asObservable() });
    const id = slowService.send(null, 'hello', []);

    chunks.next({ type: 'delta', text: 'partial ' });
    expect(slowService.isLoading(id)()).toBe(true);

    slowService.stop(id);

    expect(slowService.isLoading(id)()).toBe(false);
    expect(chunks.observed).toBe(false);
    expect(slowService.get(id)()?.messages[1].content).toBe('partial ');
  });

  it('sets isLoading while awaiting a reply and clears it once resolved', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const slowService = createChatService({ respond: () => chunks.asObservable() });

    const id = slowService.send(null, 'hi', []);
    expect(slowService.isLoading(id)()).toBe(true);

    chunks.next({ type: 'delta', text: 'done' });
    chunks.complete();
    expect(slowService.isLoading(id)()).toBe(false);
  });

  it('accumulates streamed chunks into a single assistant message as they arrive', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const streamingService = createChatService({ respond: () => chunks.asObservable() });

    const id = streamingService.send(null, 'hi', []);
    chunks.next({ type: 'delta', text: 'Hel' });
    expect(streamingService.get(id)()?.messages[1].content).toBe('Hel');

    chunks.next({ type: 'delta', text: 'lo' });
    chunks.complete();
    expect(streamingService.get(id)()?.messages[1].content).toBe('Hello');
    expect(streamingService.get(id)()?.messages.length).toBe(2);
  });

  it('shows the tool-call label while a tool is running, then clears it once text arrives', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const streamingService = createChatService({ respond: () => chunks.asObservable() });

    const id = streamingService.send(null, 'check this board', []);
    expect(streamingService.toolCallLabel(id)()).toBeNull();

    chunks.next({ type: 'toolCall', label: 'ADC Inspection Agent' });
    expect(streamingService.toolCallLabel(id)()).toBe('ADC Inspection Agent');

    chunks.next({ type: 'delta', text: 'Logged as CASE-000001.' });
    expect(streamingService.toolCallLabel(id)()).toBeNull();

    chunks.complete();
    expect(streamingService.toolCallLabel(id)()).toBeNull();
  });

  it('puts a tool result on the assistant message, before and alongside its text', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const streamingService = createChatService({ respond: () => chunks.asObservable() });
    const toolResult: ToolResult = {
      name: 'relabel_case',
      result: {
        status: 'awaiting_confirmation',
        case_number: 'CASE-000001',
        model: 'pcb_body_defect',
        model_label: 'MissingPart',
        proposed_label: 'Golden',
        reason: 'false positive',
      },
    };

    const id = streamingService.send(null, 'that is wrong, it is golden', []);
    chunks.next({ type: 'toolCall', label: 'Relabel proposal' });
    chunks.next({ type: 'toolResult', toolResult });

    // the card exists before any text does, on an assistant message of its own
    let messages = streamingService.get(id)()?.messages ?? [];
    expect(messages.length).toBe(2);
    expect(messages[1].role).toBe('assistant');
    expect(messages[1].content).toBe('');
    expect(messages[1].toolResults).toEqual([toolResult]);
    expect(streamingService.toolCallLabel(id)()).toBeNull();

    chunks.next({ type: 'delta', text: 'Shall I record Golden?' });
    chunks.complete();

    messages = streamingService.get(id)()?.messages ?? [];
    expect(messages.length).toBe(2); // the text joined the same message, not a second one
    expect(messages[1].content).toBe('Shall I record Golden?');
    expect(messages[1].toolResults).toEqual([toolResult]);
  });

  it('records the tokens a reply used on the assistant message', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const streamingService = createChatService({ respond: () => chunks.asObservable() });

    const id = streamingService.send(null, 'hi', []);
    chunks.next({ type: 'delta', text: 'Hello' });
    chunks.next({ type: 'usage', inputTokens: 120, outputTokens: 8 });
    chunks.complete();

    const messages = streamingService.get(id)()?.messages ?? [];
    expect(messages[1].content).toBe('Hello');
    expect(messages[1].usage).toEqual({ inputTokens: 120, outputTokens: 8 });
  });

  it('marks the assistant message as failed and clears loading when the responder errors', () => {
    const erroringService = createChatService({ respond: () => throwError(() => new Error('boom')) });

    const id = erroringService.send(null, 'hi', []);

    const conversation = erroringService.get(id)();
    expect(conversation?.messages[1].role).toBe('assistant');
    expect(conversation?.messages[1].error).toBe(true);
    expect(erroringService.isLoading(id)()).toBe(false);
  });

  it('keeps the text that arrived before an error and flags the message', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const flaky = createChatService({ respond: () => chunks.asObservable() });
    const id = flaky.send(null, 'hi', []);

    chunks.next({ type: 'delta', text: 'half an ans' });
    chunks.error(new Error('boom'));

    const message = flaky.get(id)()?.messages[1];
    expect(message?.content).toBe('half an ans');
    expect(message?.error).toBe(true);
  });

  it('resend() replaces a failed reply without duplicating the user message', () => {
    let attempt = 0;
    const respond = vi.fn(() =>
      attempt++ === 0
        ? throwError(() => new Error('boom'))
        : of<ChatResponderEvent>({ type: 'delta', text: 'second try' }),
    );
    const retrying = createChatService({ respond });
    const id = retrying.send(null, 'hello', []);
    expect(retrying.canResend(id)).toBe(true);

    retrying.resend(id);

    const messages = retrying.get(id)()?.messages ?? [];
    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(messages[1].content).toBe('second try');
    expect(messages[1].error).toBeUndefined();
    expect(respond).toHaveBeenCalledTimes(2);
  });

  it('resend() sends the same files again', () => {
    const respond = vi.fn().mockReturnValue(of({ type: 'delta', text: 'ok' }));
    const withFiles = createChatService({ respond });
    const image = createFile();
    const xml = createFile('inspection.xml', 'application/xml');
    const id = withFiles.send(null, 'classify', [image], [xml]);

    withFiles.resend(id);

    expect(respond).toHaveBeenLastCalledWith(id, 'classify', [image], [xml]);
  });

  it('cannot resend a message whose attachments are no longer in memory', () => {
    localStorage.setItem(
      'sentinel-chat.conversations.user-1',
      JSON.stringify([
        {
          id: 'c1',
          title: 'x',
          messages: [{ id: 'm1', role: 'user', content: 'classify', attachmentCount: 1, createdAt: 1 }],
          createdAt: 1,
          updatedAt: 1,
        },
      ]),
    );
    const reloaded = createChatService({ respond: vi.fn().mockReturnValue(of()) });

    expect(reloaded.canResend('c1')).toBe(false);
  });

  it('cannot resend while a reply is streaming', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const busy = createChatService({ respond: () => chunks.asObservable() });
    const id = busy.send(null, 'hi', []);

    expect(busy.canResend(id)).toBe(false);
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

  function serverDetail(id: string, messages: unknown[]) {
    return {
      id,
      title: 'From server',
      created_at: '2026-10-12T10:00:00Z',
      updated_at: '2026-10-12T10:05:00Z',
      messages,
    };
  }

  function serviceWithServerConversation(detail: unknown): ChatService {
    const auth = fakeAuthService();
    (auth.fetchWithAuth as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith('/api/conversations')
          ? ({ ok: true, json: () => Promise.resolve([]) } as Response)
          : ({ ok: true, json: () => Promise.resolve(detail) } as Response),
      ),
    );
    return createChatService({ respond: vi.fn().mockReturnValue(of()) }, auth);
  }

  it('restores the cards and the stopped flag of messages loaded from the server', async () => {
    const card = { name: 'create_case', result: { status: 'created', case_number: 'CASE-7' } };
    const loaded = serviceWithServerConversation(
      serverDetail('c1', [
        { id: 'm1', role: 'user', content: 'open a case', created_at: '2026-10-12T10:00:00Z' },
        {
          id: 'm2',
          role: 'assistant',
          content: 'Done',
          created_at: '2026-10-12T10:00:05Z',
          tool_results: [card, { name: 'not_a_card_we_know', result: {} }],
          interrupted: true,
        },
      ]),
    );

    loaded.ensureLoaded('c1');
    await vi.waitFor(() => expect(loaded.get('c1')()?.messages.length).toBe(2));

    const reply = loaded.get('c1')()?.messages[1];
    expect(reply?.toolResults).toEqual([card]);
    expect(reply?.interrupted).toBe(true);
  });

  it('takes the server copy when another device added messages', async () => {
    localStorage.setItem(
      `sentinel-chat.conversations.${USER.id}`,
      JSON.stringify([
        {
          id: 'c1',
          title: 'Old title',
          createdAt: 1,
          updatedAt: 1,
          messages: [
            { id: 'l1', role: 'user', content: 'one', createdAt: 1 },
            { id: 'l2', role: 'assistant', content: 'two', createdAt: 2 },
          ],
        },
      ]),
    );
    const fresh = serviceWithServerConversation(
      serverDetail('c1', [
        { id: 'a', role: 'user', content: 'one', created_at: '2026-10-12T10:00:00Z' },
        { id: 'b', role: 'assistant', content: 'two', created_at: '2026-10-12T10:00:01Z' },
        { id: 'c', role: 'user', content: 'three', created_at: '2026-10-12T10:00:02Z' },
      ]),
    );

    fresh.ensureLoaded('c1');
    await vi.waitFor(() => expect(fresh.get('c1')()?.messages.length).toBe(3));
  });

  it('keeps the local copy when it already has everything the server has', async () => {
    const auth = fakeAuthService();
    const local = createChatService({ respond: vi.fn().mockReturnValue(of({ type: 'delta', text: 'hi' })) }, auth);
    const id = local.send(null, 'hello', []);
    const kept = local.get(id)()?.messages.map((m) => m.id);
    (auth.fetchWithAuth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve(
          serverDetail(id, [
            { id: 's1', role: 'user', content: 'hello', created_at: '2026-10-12T10:00:00Z' },
            { id: 's2', role: 'assistant', content: 'hi', created_at: '2026-10-12T10:00:01Z' },
          ]),
        ),
    } as Response);

    local['hydratedConversationIds'].delete(id);
    local.ensureLoaded(id);
    await Promise.resolve();
    await Promise.resolve();

    expect(local.get(id)()?.messages.map((m) => m.id)).toEqual(kept);
  });

  it('renames a conversation and tells the server', async () => {
    const auth = fakeAuthService();
    const renamer = createChatService({ respond: vi.fn().mockReturnValue(of()) }, auth);
    const id = renamer.send(null, 'old title', []);
    (auth.fetchWithAuth as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true } as Response);

    renamer.rename(id, '  Board   A7 reflow  ');

    expect(renamer.get(id)()?.title).toBe('Board A7 reflow');
    const call = (auth.fetchWithAuth as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(call[0]).toContain(`/api/conversations/${id}`);
    expect(call[1].method).toBe('PATCH');
    expect(JSON.parse(call[1].body)).toEqual({ title: 'Board A7 reflow' });
  });

  it('puts the old title back when the server refuses a rename', async () => {
    const auth = fakeAuthService();
    const renamer = createChatService({ respond: vi.fn().mockReturnValue(of()) }, auth);
    const id = renamer.send(null, 'old title', []);
    (auth.fetchWithAuth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 500,
    } as Response);

    renamer.rename(id, 'new title');
    await vi.waitFor(() => expect(renamer.get(id)()?.title).toBe('old title'));
  });

  it('stop() marks the partial reply as stopped', () => {
    const chunks = new Subject<ChatResponderEvent>();
    const slow = createChatService({ respond: () => chunks.asObservable() });
    const id = slow.send(null, 'hello', []);
    chunks.next({ type: 'delta', text: 'partial' });

    slow.stop(id);

    expect(slow.get(id)()?.messages[1].interrupted).toBe(true);
  });

  it('keeps a user-facing error message on the failed reply', () => {
    const failing = createChatService({
      respond: () => throwError(() => new ChatResponderError('Your session has expired.')),
    });

    const id = failing.send(null, 'hi', []);

    expect(failing.get(id)()?.messages[1].errorMessage).toBe('Your session has expired.');
  });

  it('does not show an unexpected error\'s text to the user', () => {
    const failing = createChatService({
      respond: () => throwError(() => new Error('TypeError: x is undefined at line 4')),
    });

    const id = failing.send(null, 'hi', []);

    expect(failing.get(id)()?.messages[1].errorMessage).toBeUndefined();
  });

  it('counts attachments on the message so they survive a reload', () => {
    const id = service.send(null, 'see attached', [createFile()], [createFile('i.xml', 'application/xml')]);

    expect(service.get(id)()?.messages[0].attachmentCount).toBe(2);
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
