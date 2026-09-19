import { firstValueFrom } from 'rxjs';
import { toArray } from 'rxjs/operators';
import { ChatResponderEvent, MockChatResponder } from './chat-responder';

function textOf(events: ChatResponderEvent[]): string {
  return events.map((event) => (event.type === 'delta' ? event.text : '')).join('');
}

describe('MockChatResponder', () => {
  it('streams the reply as more than one chunk', async () => {
    const events = await firstValueFrom(
      new MockChatResponder().respond('c1', 'hello', []).pipe(toArray()),
    );

    expect(events.length).toBeGreaterThan(1);
    expect(textOf(events)).toContain('You said: "hello"');
  });

  it('notes attached images in the reply', async () => {
    const file = new File(['x'], 'board.png', { type: 'image/png' });
    const events = await firstValueFrom(
      new MockChatResponder().respond('c1', 'see attached', [file]).pipe(toArray()),
    );

    expect(textOf(events)).toContain('1 image attached');
  });
});
