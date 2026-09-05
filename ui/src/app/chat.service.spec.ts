import { of } from 'rxjs';
import { vi } from 'vitest';
import { ChatResponder } from './chat-responder';
import { ChatService } from './chat.service';

function createFile(name = 'board.png', type = 'image/png'): File {
  return new File(['fake-bytes'], name, { type });
}

describe('ChatService', () => {
  let responder: ChatResponder;
  let service: ChatService;

  beforeEach(() => {
    localStorage.clear();
    responder = { respond: vi.fn().mockReturnValue(of('mock reply')) };
    service = new ChatService(responder);
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
    // A responder that never emits, so we can observe the loading state mid-flight.
    let resolve!: (value: string) => void;
    const pending = new Promise<string>((r) => (resolve = r));
    const slowResponder: ChatResponder = {
      respond: () => ({
        subscribe: (cb: (v: string) => void) => {
          pending.then(cb);
        },
      }) as any,
    };
    const slowService = new ChatService(slowResponder);

    const id = slowService.send(null, 'hi', []);
    expect(slowService.isLoading(id)()).toBe(true);

    resolve('done');
    return pending.then(() => {
      expect(slowService.isLoading(id)()).toBe(false);
    });
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

  it('persists text history to localStorage without image object URLs', () => {
    service.send(null, 'see attached', [createFile()]);

    const raw = localStorage.getItem('sentinel-chat.conversations');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed[0].messages[0].content).toBe('see attached');
    expect(parsed[0].messages[0].imageUrls).toBeUndefined();
  });

  it('restores conversations from localStorage on construction', () => {
    service.send(null, 'remembered', []);

    const restored = new ChatService(responder);
    expect(restored.list()().length).toBe(1);
    expect(restored.list()()[0].title).toBe('remembered');
  });
});
