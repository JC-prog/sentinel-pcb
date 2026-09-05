import { firstValueFrom } from 'rxjs';
import { toArray } from 'rxjs/operators';
import { vi } from 'vitest';
import { HttpChatResponder } from './http-chat-responder';

function sseResponse(body: string, ok = true): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return { ok, status: ok ? 200 : 500, body: stream } as unknown as Response;
}

describe('HttpChatResponder', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('emits one value per delta frame, in order, then completes', async () => {
    const body =
      'event: delta\ndata: {"text":"Hel"}\n\n' +
      'event: delta\ndata: {"text":"lo"}\n\n' +
      'event: done\ndata: {}\n\n';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(body)));

    const chunks = await firstValueFrom(
      new HttpChatResponder().respond('c1', 'hi', []).pipe(toArray()),
    );

    expect(chunks).toEqual(['Hel', 'lo']);
  });

  it('errors the observable when the stream sends an error frame', async () => {
    const body = 'event: error\ndata: {"message":"upstream failed"}\n\n';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(body)));

    await expect(
      firstValueFrom(new HttpChatResponder().respond('c1', 'hi', []).pipe(toArray())),
    ).rejects.toThrow('upstream failed');
  });

  it('uploads each image before opening the chat stream', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/api/uploads')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ id: 'img-1', url: '/api/uploads/img-1.png' }),
        } as Response);
      }
      return Promise.resolve(sseResponse('event: done\ndata: {}\n\n'));
    });
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'board.png', { type: 'image/png' });
    await firstValueFrom(
      new HttpChatResponder().respond('c1', 'see attached', [file]).pipe(toArray()),
    );

    const chatCall = fetchMock.mock.calls.find(([url]) => url.endsWith('/api/chat/stream'));
    const requestBody = JSON.parse(chatCall![1].body);
    expect(requestBody.image_ids).toEqual(['img-1']);
  });
});
