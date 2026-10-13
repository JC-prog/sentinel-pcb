import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { Chat } from './chat';
import { AuthService, AuthUser } from '../auth.service';
import { CHAT_RESPONDER, ChatResponderError, ChatResponderEvent } from './chat-responder';
import { ChatService } from './chat.service';

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
  role: 'qa',
};

/** Same reasoning as chat.service.spec.ts's fakeAuthService(). */
function fakeAuthService(role: AuthUser['role'] = 'qa'): AuthService {
  return {
    currentUser: () => ({ ...USER, role }),
    fetchWithAuth: vi.fn().mockResolvedValue({ ok: false } as Response),
  } as unknown as AuthService;
}

describe('Chat', () => {
  let fixture: ComponentFixture<Chat>;

  beforeEach(() => {
    localStorage.clear();
  });

  async function setup(route: FakeActivatedRoute, role: AuthUser['role'] = 'qa') {
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'mock reply' }) } },
        { provide: AuthService, useValue: fakeAuthService(role) },
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
    expect(compiled.textContent).toContain("Hi, I'm the Sentinel PCB assistant");
  });

  it('hides the Admin-only suggestions from QA and shows them to an Admin', async () => {
    await setup(new FakeActivatedRoute(), 'qa');
    expect((fixture.nativeElement as HTMLElement).textContent).not.toContain('Model status and queue');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('What runs do I have?');

    TestBed.resetTestingModule();
    await setup(new FakeActivatedRoute(), 'admin');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Model status and queue');
  });

  it('groups the empty-state tiles by what they are for', async () => {
    await setup(new FakeActivatedRoute(), 'admin');

    const sections = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('section[aria-label]'),
    ).map((section) => ({
      title: section.getAttribute('aria-label'),
      tiles: Array.from(section.querySelectorAll('button')).map((b) =>
        b.querySelector('span.block')?.textContent?.trim(),
      ),
    }));

    expect(sections).toEqual([
      { title: 'Inspect', tiles: ['Classify an inspection image'] },
      {
        title: 'Look up',
        tiles: ['Look up a sample', "What's waiting for review?", 'What runs do I have?'],
      },
      {
        title: 'Model health',
        tiles: ["Review a run's corrections", 'Are corrections piling up?', 'Model status and queue'],
      },
    ]);
  });

  it('shows QA the same tiles without the Admin-only one', async () => {
    await setup(new FakeActivatedRoute(), 'qa');

    const tiles = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('section[aria-label] button'),
    ).map((b) => b.querySelector('span.block')?.textContent?.trim());

    expect(tiles).toHaveLength(6);
    expect(tiles).not.toContain('Model status and queue');
    // a label can only be corrected after an inspection, so it is not an opening move
    expect(tiles).not.toContain('Correct a wrong label');
    expect(tiles).not.toContain('Draft a retraining plan');
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
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'mock reply' }) } },
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
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'mock reply' }) } },
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

  it('renders assistant markdown instead of showing it raw', async () => {
    await TestBed.configureTestingModule({
      providers: [
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'a **bold** claim\n\n- one\n- two' }) } },
        { provide: AuthService, useValue: fakeAuthService() },
      ],
    }).compileComponents();
    const probe = TestBed.inject(ChatService);
    const id = probe.send(null, 'hi', []);

    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: ChatService, useValue: probe },
        { provide: ActivatedRoute, useValue: new FakeActivatedRoute({ id }) },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('span.font-semibold')?.textContent).toBe('bold');
    expect(compiled.querySelectorAll('ul li').length).toBe(2);
    expect(compiled.textContent).not.toContain('**');
  });

  it('shows a Stop button while a reply is streaming and stops it on click', async () => {
    const chunks = new Subject<ChatResponderEvent>();
    await TestBed.configureTestingModule({
      providers: [
        { provide: CHAT_RESPONDER, useValue: { respond: () => chunks.asObservable() } },
        { provide: AuthService, useValue: fakeAuthService() },
      ],
    }).compileComponents();
    const probe = TestBed.inject(ChatService);
    const id = probe.send(null, 'hi', []);
    chunks.next({ type: 'delta', text: 'working' });

    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: ChatService, useValue: probe },
        { provide: ActivatedRoute, useValue: new FakeActivatedRoute({ id }) },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const stop = compiled.querySelector<HTMLButtonElement>('button[aria-label="Stop generating"]');
    expect(stop).toBeTruthy();

    stop!.click();
    fixture.detectChanges();

    expect(probe.isLoading(id)()).toBe(false);
    expect(compiled.querySelector('button[aria-label="Stop generating"]')).toBeNull();
    expect(compiled.textContent).toContain('working');
  });

  /** Opens the Chat component on a conversation built by sending through a probe ChatService. */
  async function openConversation(
    respond: () => unknown,
    prepare?: (chatService: ChatService) => string,
  ): Promise<{ id: string; chatService: ChatService }> {
    await TestBed.configureTestingModule({
      providers: [
        { provide: CHAT_RESPONDER, useValue: { respond } },
        { provide: AuthService, useValue: fakeAuthService() },
      ],
    }).compileComponents();
    const probe = TestBed.inject(ChatService);
    const id = prepare ? prepare(probe) : probe.send(null, 'hi', []);

    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        provideRouter([]),
        { provide: ChatService, useValue: probe },
        { provide: ActivatedRoute, useValue: new FakeActivatedRoute({ id }) },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Chat);
    fixture.detectChanges();
    return { id, chatService: probe };
  }

  function buttonWithText(text: string): HTMLButtonElement | undefined {
    return Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find((b) =>
      b.textContent?.includes(text),
    );
  }

  it('shows a retry control for a failed reply and resends on click', async () => {
    let attempt = 0;
    const { id, chatService } = await openConversation(() =>
      attempt++ === 0 ? throwError(() => new Error('boom')) : of({ type: 'delta', text: 'recovered' }),
    );
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('[role="alert"]')?.textContent).toContain('went wrong');

    buttonWithText('Retry')!.click();
    fixture.detectChanges();

    expect(chatService.get(id)()?.messages[1].content).toBe('recovered');
    expect(compiled.querySelector('[role="alert"]')).toBeNull();
  });

  it('offers Regenerate on the last reply and Copy / Edit on messages', async () => {
    await openConversation(() => of({ type: 'delta', text: 'a reply' }));
    const labels = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).map((b) => b.getAttribute('aria-label'));

    expect(labels).toContain('Regenerate reply');
    expect(labels).toContain('Edit as a new message');
    expect(labels.filter((label) => label === 'Copy message').length).toBe(2);
  });

  it('puts a sent message back in the box when Edit is used', async () => {
    await openConversation(() => of({ type: 'delta', text: 'a reply' }));
    const edit = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      'button[aria-label="Edit as a new message"]',
    );

    edit!.click();

    expect(fixture.componentInstance['draftText']()).toBe('hi');
  });

  it('shows one-click follow-ups under an inspection card and sends them', async () => {
    const inspection = {
      name: 'inspect_image',
      result: {
        case_created: false,
        verdict: 'review_required',
        review_required: true,
        review_reasons: ['low confidence'],
        inspection_summary: null,
        region: null,
        defect: null,
        measurement_validation: null,
      },
    };
    const { id, chatService } = await openConversation(() =>
      of({ type: 'toolResult', toolResult: inspection }),
    );
    const chip = buttonWithText('Open a case for this inspection.');
    expect(chip).toBeTruthy();

    chip!.click();

    const sent = chatService.get(id)()?.messages.filter((m) => m.role === 'user');
    expect(sent?.map((m) => m.content)).toContain('Open a case for this inspection.');
  });

  it('sends a self-contained suggestion immediately', async () => {
    await setup(new FakeActivatedRoute());
    const chatService = TestBed.inject(ChatService);
    vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const suggestion = fixture.componentInstance['suggestions'].find((s) => s.send)!;

    fixture.componentInstance.useSuggestion(suggestion);

    const conversations = chatService.list()();
    expect(conversations.length).toBe(1);
    expect(conversations[0].messages[0].content).toBe(suggestion.prompt);
  });

  it('fills an editable suggestion without sending it', async () => {
    await setup(new FakeActivatedRoute());
    const chatService = TestBed.inject(ChatService);
    const suggestion = fixture.componentInstance['suggestions'].find(
      (s) => !s.send && !s.needsAttachment,
    )!;

    fixture.componentInstance.useSuggestion(suggestion);

    expect(fixture.componentInstance['draftText']()).toBe(suggestion.prompt);
    expect(chatService.list()().length).toBe(0);
  });

  it('opens the attach picker for a suggestion that needs an image, without sending', async () => {
    await setup(new FakeActivatedRoute());
    const chatService = TestBed.inject(ChatService);
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      'input[type="file"]',
    )!;
    const click = vi.spyOn(input, 'click');
    const suggestion = fixture.componentInstance['suggestions'].find((s) => s.needsAttachment)!;

    fixture.componentInstance.useSuggestion(suggestion);

    expect(click).toHaveBeenCalled();
    expect(chatService.list()().length).toBe(0);
  });

  it('routes images and XML from the single attach picker to their own lists', async () => {
    await setup(new FakeActivatedRoute());
    const image = new File(['x'], 'a.png', { type: 'image/png' });
    const xml = new File(['<x/>'], 'inspection.xml', { type: 'application/xml' });
    const other = new File(['x'], 'notes.txt', { type: 'text/plain' });

    fixture.componentInstance.onFilesSelected({
      target: { files: [image, xml, other], value: '' },
    } as unknown as Event);

    expect(fixture.componentInstance['pendingImages']().map((i) => i.file)).toEqual([image]);
    expect(fixture.componentInstance['pendingXmlFiles']().map((x) => x.file)).toEqual([xml]);
  });

  it('attaches an image pasted into the message box', async () => {
    await setup(new FakeActivatedRoute());
    const image = new File(['x'], 'shot.png', { type: 'image/png' });
    const preventDefault = vi.fn();

    fixture.componentInstance.onPaste({
      clipboardData: { files: [image] },
      preventDefault,
    } as unknown as ClipboardEvent);

    expect(fixture.componentInstance['pendingImages']().map((i) => i.file)).toEqual([image]);
    expect(preventDefault).toHaveBeenCalled();
  });

  it('leaves pasted text to the textarea', async () => {
    await setup(new FakeActivatedRoute());
    const preventDefault = vi.fn();

    fixture.componentInstance.onPaste({
      clipboardData: { files: [] },
      preventDefault,
    } as unknown as ClipboardEvent);

    expect(preventDefault).not.toHaveBeenCalled();
  });

  it('does not send a second message while a reply is streaming', async () => {
    const chunks = new Subject<ChatResponderEvent>();
    const { id, chatService } = await openConversation(() => chunks.asObservable());
    expect(chatService.isLoading(id)()).toBe(true);

    fixture.componentInstance['draftText'].set('another');
    fixture.componentInstance.send();

    expect(chatService.get(id)()?.messages.filter((m) => m.role === 'user').length).toBe(1);
  });

  it('does not send when Enter confirms an IME candidate', async () => {
    await setup(new FakeActivatedRoute());
    const chatService = TestBed.inject(ChatService);
    fixture.componentInstance['draftText'].set('こんにちは');
    const preventDefault = vi.fn();

    fixture.componentInstance.onTextareaKeydown({
      key: 'Enter',
      shiftKey: false,
      isComposing: true,
      keyCode: 229,
      preventDefault,
    } as unknown as KeyboardEvent);

    expect(chatService.list()().length).toBe(0);
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it('says why a dropped file was not attached', async () => {
    await setup(new FakeActivatedRoute());
    const note = new File(['x'], 'notes.txt', { type: 'text/plain' });
    const image = new File(['x'], 'a.png', { type: 'image/png' });

    fixture.componentInstance.onDrop(fakeDropEvent([note, image]));
    fixture.detectChanges();

    expect(fixture.componentInstance['pendingImages']().length).toBe(1);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Not attached: notes.txt is not an image or an XML file.',
    );
  });

  it('refuses a file over 20 MB before uploading it', async () => {
    await setup(new FakeActivatedRoute());
    const huge = new File(['x'], 'huge.png', { type: 'image/png' });
    Object.defineProperty(huge, 'size', { value: 21 * 1024 * 1024 });

    fixture.componentInstance.onDrop(fakeDropEvent([huge]));

    expect(fixture.componentInstance['pendingImages']()).toEqual([]);
    expect(fixture.componentInstance['attachmentNotice']()).toContain('huge.png is larger than 20 MB');
  });

  it('shows the specific reason a reply failed', async () => {
    await openConversation(() =>
      throwError(() => new ChatResponderError('Too many requests. Wait a moment and try again.')),
    );

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]')?.textContent,
    ).toContain('Too many requests');
  });

  it('marks a stopped reply and offers to continue it', async () => {
    const chunks = new Subject<ChatResponderEvent>();
    const { id, chatService } = await openConversation(() => chunks.asObservable());
    chunks.next({ type: 'delta', text: 'half an answer' });
    chatService.stop(id);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Reply stopped');
    expect(buttonWithText('Please continue from where you stopped.')).toBeTruthy();
  });

  it('offers approve / override / relabel right after a case is created', async () => {
    const created = {
      name: 'create_case',
      result: {
        status: 'created',
        case_number: 'CASE-12',
        verdict: 'review_required',
        region: 'Body',
        defect: 'Bridge',
        defect_confidence: 0.61,
      },
    };
    await openConversation(() => of({ type: 'toolResult', toolResult: created }));

    expect(buttonWithText('Approve case')).toBeUndefined();
    expect(buttonWithText('Override case')).toBeUndefined();
    expect(buttonWithText('The label on case CASE-12 is wrong')).toBeTruthy();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('CASE-12');
  });

  it('offers Yes and No when the assistant asks the user to confirm in plain text', async () => {
    await openConversation(() =>
      of({
        type: 'delta',
        text: 'I will propose relabeling case CASE-1 to "Wrong part". Please confirm if you would like to proceed.',
      }),
    );

    expect(buttonWithText('Yes')).toBeTruthy();
    expect(buttonWithText('No')).toBeTruthy();
  });

  it('offers Yes and No for a question that asks whether to proceed', async () => {
    await openConversation(() =>
      of({ type: 'delta', text: 'The model said Tombstone at 71%.\n\nShall I save it as a case?' }),
    );

    expect(buttonWithText('Yes')).toBeTruthy();
    expect(buttonWithText('No')).toBeTruthy();
  });

  it('does not offer Yes and No for a reply that is not asking anything', async () => {
    await openConversation(() =>
      of({ type: 'delta', text: 'You can confirm the retraining plan later in the Models tab.' }),
    );

    expect(buttonWithText('Yes')).toBeUndefined();
    expect(buttonWithText('No')).toBeUndefined();
  });

  it('sends Yes when its chip is clicked', async () => {
    const { id, chatService } = await openConversation(() =>
      of({ type: 'delta', text: 'Do you want me to save this as a case?' }),
    );

    buttonWithText('Yes')!.click();

    const sent = chatService.get(id)()?.messages.filter((m) => m.role === 'user');
    expect(sent?.map((m) => m.content)).toContain('Yes');
  });

  it('does not offer a relabel for a case that has no defect label', async () => {
    const created = {
      name: 'create_case',
      result: {
        status: 'created',
        case_number: 'CASE-14',
        verdict: 'review_required',
        region: 'Body',
        defect: null,
        defect_confidence: null,
      },
    };
    await openConversation(() => of({ type: 'toolResult', toolResult: created }));

    expect(buttonWithText('The label on case CASE-14')).toBeUndefined();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('no label to correct');
  });

  it('only offers a relabel for an accepted case', async () => {
    const created = {
      name: 'create_case',
      result: {
        status: 'created',
        case_number: 'CASE-13',
        verdict: 'accepted',
        region: 'Body',
        defect: 'Golden',
        defect_confidence: 0.97,
      },
    };
    await openConversation(() => of({ type: 'toolResult', toolResult: created }));

    expect(buttonWithText('Approve case')).toBeUndefined();
    expect(buttonWithText('The label on case CASE-13 is wrong')).toBeTruthy();
  });

  it('keeps the live region out of the streaming message list', async () => {
    await openConversation(() => of({ type: 'delta', text: 'a reply' }));
    const log = (fixture.nativeElement as HTMLElement).querySelector('[role="log"]')!;

    expect(log.getAttribute('aria-live')).toBeNull();
    expect(log.querySelector('[role="status"][aria-live="polite"]')).toBeTruthy();
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

  function fakeFileInputEvent(files: File[]): Event {
    return { target: { files, value: '' } } as unknown as Event;
  }

  it('adds a selected XML file to the pending attachments', async () => {
    await setup(new FakeActivatedRoute());
    const file = new File(['<x/>'], 'inspection.xml', { type: 'application/xml' });

    fixture.componentInstance.onXmlFilesSelected(fakeFileInputEvent([file]));

    expect(fixture.componentInstance['pendingXmlFiles']().map((xml) => xml.file)).toEqual([file]);
  });

  it('ignores a selected non-XML file in the XML picker', async () => {
    await setup(new FakeActivatedRoute());
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });

    fixture.componentInstance.onXmlFilesSelected(fakeFileInputEvent([file]));

    expect(fixture.componentInstance['pendingXmlFiles']()).toEqual([]);
  });

  it('removes a pending XML file by index', async () => {
    await setup(new FakeActivatedRoute());
    const file = new File(['<x/>'], 'inspection.xml', { type: 'application/xml' });
    fixture.componentInstance.onXmlFilesSelected(fakeFileInputEvent([file]));

    fixture.componentInstance.removePendingXml(0);

    expect(fixture.componentInstance['pendingXmlFiles']()).toEqual([]);
  });

  it('sends a message with no text when only an XML file is attached', async () => {
    await setup(new FakeActivatedRoute());
    const router = TestBed.inject(Router);
    const chatService = TestBed.inject(ChatService);
    vi.spyOn(router, 'navigate');
    const file = new File(['<x/>'], 'inspection.xml', { type: 'application/xml' });
    fixture.componentInstance.onXmlFilesSelected(fakeFileInputEvent([file]));

    fixture.componentInstance.send();

    expect(chatService.list()().length).toBe(1);
    expect(fixture.componentInstance['pendingXmlFiles']()).toEqual([]);
  });
});
