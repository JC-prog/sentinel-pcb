import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { Chat } from './chat';
import { CHAT_RESPONDER } from '../chat-responder';
import { ChatService } from '../chat.service';

class FakeActivatedRoute {
  readonly paramMap;
  constructor(params: Record<string, string> = {}) {
    this.paramMap = of(convertToParamMap(params));
  }
}

describe('Chat', () => {
  let fixture: ComponentFixture<Chat>;

  beforeEach(() => {
    localStorage.clear();
  });

  async function setup(route: FakeActivatedRoute) {
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of('mock reply') } },
        { provide: ActivatedRoute, useValue: route },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();
  }

  it('should create', async () => {
    await setup(new FakeActivatedRoute());
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('shows the empty-state hero when there is no conversation yet', async () => {
    await setup(new FakeActivatedRoute());
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('What can I help with?');
  });

  it('creates a conversation and navigates to it when sending the first message', async () => {
    await setup(new FakeActivatedRoute());
    const router = TestBed.inject(Router);
    const chatService = TestBed.inject(ChatService);
    vi.spyOn(router, 'navigate');

    fixture.componentInstance['draftText'].set('Hello there');
    fixture.componentInstance.send();
    fixture.detectChanges();

    const conversations = chatService.list()();
    expect(conversations.length).toBe(1);
    expect(conversations[0].messages[0].content).toBe('Hello there');
    expect(router.navigate).toHaveBeenCalledWith(['/c', conversations[0].id]);
  });

  it('renders existing messages and the mocked assistant reply', async () => {
    const chatServiceProbe = new ChatService({ respond: () => of('mock reply') });
    const id = chatServiceProbe.send(null, 'Hello', []);

    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of('mock reply') } },
        { provide: ChatService, useValue: chatServiceProbe },
        { provide: ActivatedRoute, useValue: new FakeActivatedRoute({ id }) },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Hello');
    expect(compiled.textContent).toContain('mock reply');
  });

  it('does not send an empty message with no attached images', async () => {
    await setup(new FakeActivatedRoute());
    const chatService = TestBed.inject(ChatService);

    fixture.componentInstance.send();

    expect(chatService.list()().length).toBe(0);
  });
});
