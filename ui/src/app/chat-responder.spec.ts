import { firstValueFrom } from 'rxjs';
import { toArray } from 'rxjs/operators';
import { MockChatResponder } from './chat-responder';

describe('MockChatResponder', () => {
  it('streams the reply as more than one chunk', async () => {
    const chunks = await firstValueFrom(
      new MockChatResponder().respond('c1', 'hello', []).pipe(toArray()),
    );

    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks.join('')).toContain('You said: "hello"');
  });

  it('notes attached images in the reply', async () => {
    const file = new File(['x'], 'board.png', { type: 'image/png' });
    const chunks = await firstValueFrom(
      new MockChatResponder().respond('c1', 'see attached', [file]).pipe(toArray()),
    );

    expect(chunks.join('')).toContain('1 image attached');
  });
});
