import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { Chat } from './chat';
import { AuthService, AuthUser } from '../auth.service';
import { CHAT_RESPONDER } from '../chat-responder';
import { ChatService } from '../chat.service';

class FakeActivatedRoute {
  readonly paramMap;
  constructor(params: Record<string, string> = {}) {
    this.paramMap = of(convertToParamMap(params));
  }
}

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
  departmentShift: 'QA Day Shift',
  role: 'qa',
};

/** Same reasoning as chat.service.spec.ts's fakeAuthService(). */
function fakeAuthService(): AuthService {
  return {
    currentUser: () => USER,
    fetchWithAuth: vi.fn().mockResolvedValue({ ok: false } as Response),
  } as unknown as AuthService;
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
        { provide: AuthService, useValue: fakeAuthService() },
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
    await TestBed.configureTestingModule({
      providers: [
        { provide: CHAT_RESPONDER, useValue: { respond: () => of('mock reply') } },
        { provide: AuthService, useValue: fakeAuthService() },
      ],
    }).compileComponents();
    const chatServiceProbe = TestBed.inject(ChatService);
    const id = chatServiceProbe.send(null, 'Hello', []);

    TestBed.resetTestingModule();
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

  function fakeDropEvent(files: File[]): DragEvent {
    return {
      preventDefault: vi.fn(),
      dataTransfer: { files },
    } as unknown as DragEvent;
  }

  it('adds a dropped image file to the pending attachments', async () => {
    await setup(new FakeActivatedRoute());
    const file = new File(['x'], 'board.png', { type: 'image/png' });

    fixture.componentInstance.onDrop(fakeDropEvent([file]));

    expect(fixture.componentInstance['pendingImages']().map((image) => image.file)).toEqual([
      file,
    ]);
  });

  it('adds all dropped image files when multiple are dropped at once', async () => {
    await setup(new FakeActivatedRoute());
    const first = new File(['x'], 'a.png', { type: 'image/png' });
    const second = new File(['y'], 'b.jpg', { type: 'image/jpeg' });

    fixture.componentInstance.onDrop(fakeDropEvent([first, second]));

    expect(fixture.componentInstance['pendingImages']().map((image) => image.file)).toEqual([
      first,
      second,
    ]);
  });

  it('ignores a dropped non-image file', async () => {
    await setup(new FakeActivatedRoute());
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });

    fixture.componentInstance.onDrop(fakeDropEvent([file]));

    expect(fixture.componentInstance['pendingImages']()).toEqual([]);
  });

  it('shows the drop overlay on dragenter and hides it on the matching dragleave', async () => {
    await setup(new FakeActivatedRoute());
    const event = { preventDefault: vi.fn() } as unknown as DragEvent;

    fixture.componentInstance.onDragEnter(event);
    expect(fixture.componentInstance['isDraggingOver']()).toBe(true);

    fixture.componentInstance.onDragLeave(event);
    expect(fixture.componentInstance['isDraggingOver']()).toBe(false);
  });

  it('keeps the overlay visible while dragging over a nested child element', async () => {
    await setup(new FakeActivatedRoute());
    const event = { preventDefault: vi.fn() } as unknown as DragEvent;

    fixture.componentInstance.onDragEnter(event); // enters the drop zone
    fixture.componentInstance.onDragEnter(event); // enters a child element
    fixture.componentInstance.onDragLeave(event); // leaves the child element
    expect(fixture.componentInstance['isDraggingOver']()).toBe(true);

    fixture.componentInstance.onDragLeave(event); // leaves the drop zone itself
    expect(fixture.componentInstance['isDraggingOver']()).toBe(false);
  });

  it('hides the overlay after a drop', async () => {
    await setup(new FakeActivatedRoute());
    const enterEvent = { preventDefault: vi.fn() } as unknown as DragEvent;
    fixture.componentInstance.onDragEnter(enterEvent);

    fixture.componentInstance.onDrop(fakeDropEvent([]));

    expect(fixture.componentInstance['isDraggingOver']()).toBe(false);
  });
});
