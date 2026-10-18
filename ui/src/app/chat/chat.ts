import { CommonModule } from '@angular/common';
import {
  Component,
  ElementRef,
  Signal,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs/operators';
import { AuthService } from '../auth.service';
import { BriefingService } from './briefing.service';
import { ChatService } from './chat.service';
import { Icon, IconName } from './icons/icon';
import { MarkdownView } from './markdown/markdown-view';
import { ChatMessage, Conversation } from './models/chat.models';
import { ToolResultCard } from './tool-result-card/tool-result-card';

/** Within this many pixels of the bottom counts as "reading the latest" - new text keeps the view pinned. */
const STICK_TO_BOTTOM_PX = 80;
/** Matches the textarea's max-h-40. */
const MAX_INPUT_HEIGHT_PX = 160;
const COPIED_FEEDBACK_MS = 1500;
const NOTICE_MS = 8000;
/** Larger files are refused before upload rather than failing partway. */
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

interface PendingImage {
  file: File;
  previewUrl: string;
}

interface PendingXml {
  file: File;
}

type SuggestionGroup = 'Inspect' | 'Look up' | 'Model health';

interface Suggestion {
  /** The heading it sits under on the empty state. */
  group: SuggestionGroup;
  icon: IconName;
  label: string;
  hint: string;
  prompt: string;
  /** Needs a file: fills the prompt and opens the attach picker instead of sending. */
  needsAttachment?: boolean;
  /** Complete as written: sent on click. Otherwise the prompt is only put in the box to edit. */
  send?: boolean;
  /** Needs a tool only an Admin is offered; hidden from QA so a suggestion never ends in a refusal. */
  adminOnly?: boolean;
}

function isXml(file: File): boolean {
  return file.name.toLowerCase().endsWith('.xml');
}

@Component({
  imports: [CommonModule, FormsModule, Icon, MarkdownView, ToolResultCard],
  selector: 'app-chat',
  styleUrl: './chat.css',
  templateUrl: './chat.html',
})
export class Chat {
  private readonly scrollContainer = viewChild<ElementRef<HTMLElement>>('scrollContainer');
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>;

  protected readonly suggestions: Suggestion[] = [
    {
      group: 'Inspect',
      icon: 'paperclip',
      label: 'Classify an inspection image',
      hint: 'Attach an AOI image (and its XML, if you have one)',
      prompt: 'Classify this AOI inspection image for defects.',
      needsAttachment: true,
    },
    {
      group: 'Look up',
      icon: 'search',
      label: 'Look up a sample',
      hint: "Verdict, Agent 2's review and the decision",
      prompt: 'Show me sample S000001.',
    },
    {
      group: 'Look up',
      icon: 'file',
      label: "What's waiting for review?",
      hint: 'Samples flagged in the latest run',
      prompt: 'Which samples are waiting for review?',
      send: true,
    },
    {
      group: 'Look up',
      icon: 'menu',
      label: 'What runs do I have?',
      hint: 'The stored Work-tab runs, to pick a run id',
      prompt: 'Which Work-tab runs are stored?',
      send: true,
    },
    {
      group: 'Model health',
      icon: 'check',
      label: "Review a run's corrections",
      hint: 'What the operator corrected, and what is queued',
      prompt: "Show the drift for the latest run and which of the operator's corrections are already queued for retraining.",
      send: true,
    },
    {
      group: 'Model health',
      icon: 'arrow-up',
      label: 'Are corrections piling up?',
      hint: 'Corrections to a model this week against last',
      prompt: 'Are corrections to the defect models piling up?',
      send: true,
    },
    {
      group: 'Model health',
      icon: 'settings',
      label: 'Model status and queue',
      hint: 'Live versions, open drift reports, retraining queue',
      prompt: 'What is the model status, and what is in the retraining queue?',
      send: true,
      adminOnly: true,
    },
  ];

  /** The tiles this user can use, grouped by what they are for, in the order they are declared. */
  protected readonly suggestionGroups = computed(() => {
    const groups: { title: SuggestionGroup; items: Suggestion[] }[] = [];
    for (const suggestion of this.visibleSuggestions()) {
      const group = groups.find((g) => g.title === suggestion.group);
      if (group) {
        group.items.push(suggestion);
      } else {
        groups.push({ title: suggestion.group, items: [suggestion] });
      }
    }
    return groups;
  });

  private readonly auth = inject(AuthService);
  /** The suggestions this user can use: Admin-only ones are not shown to QA. */
  protected readonly visibleSuggestions = computed(() =>
    this.suggestions.filter((s) => !s.adminOnly || this.auth.currentUser()?.role === 'admin'),
  );

  private readonly briefingService = inject(BriefingService);
  /** What is waiting for an Admin in the Models tab, as lines for the opening screen. */
  protected readonly briefingItems = signal<string[]>([]);

  protected readonly draftText = signal('');
  protected readonly pendingImages = signal<PendingImage[]>([]);
  /** Inspection XML(s), for inspect_image (app/chat/agents/inspection_agent/) - optional, at most what the
   * user explicitly attaches; never inferred from a dropped/pasted image the way pendingImages is. */
  protected readonly pendingXmlFiles = signal<PendingXml[]>([]);
  protected readonly isDraggingOver = signal(false);
  /** dragenter/dragleave fire once per element boundary crossed, including children of the
   * drop zone - a single dragleave doesn't mean the pointer truly left it. Counting enter/leave
   * pairs and only clearing state at zero avoids the overlay flickering off while dragging over
   * a child element. */
  private dragDepth = 0;
  /** True while the view should follow new text; false once the user scrolls up to read. */
  protected readonly stickToBottom = signal(true);
  /** Set by send(): the input is re-created when the first message swaps the empty state for the
   * thread, so focus is restored once the new element exists. */
  private refocusInput = false;
  /** The message whose Copy button was just used - shows a check for a moment. */
  protected readonly copiedMessageId = signal<string | null>(null);
  /** Why a dropped or picked file was not attached; shown above the message box. */
  protected readonly attachmentNotice = signal<string | null>(null);
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;
  /** For screen readers: the reply's progress in a short sentence, not every streamed chunk. */
  protected readonly announcement = signal('');
  private wasLoading = false;

  private readonly conversationId: Signal<string | null>;
  protected readonly conversation: Signal<Conversation | undefined>;
  protected readonly isLoading: Signal<boolean>;
  protected readonly toolCallLabel: Signal<string | null>;
  /** Quick replies for the last assistant message's tool card - see followUpsFor(). */
  protected readonly followUps: Signal<string[]>;

  protected openModels(): void {
    void this.router.navigateByUrl('/models');
  }

  constructor(
    private readonly route: ActivatedRoute,
    private readonly router: Router,
    private readonly chatService: ChatService,
  ) {
    this.conversationId = toSignal(
      this.route.paramMap.pipe(map((params) => params.get('id'))),
      { initialValue: null },
    );

    // An Admin's opening screen says what is waiting for them; QA has nothing to ask.
    if (this.auth.currentUser()?.role === 'admin') {
      void this.briefingService.load().then((briefing) => {
        this.briefingItems.set(briefing ? BriefingService.items(briefing) : []);
      });
    }

    this.conversation = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.get(id)() : undefined;
    });

    this.isLoading = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.isLoading(id)() : false;
    });

    this.toolCallLabel = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.toolCallLabel(id)() : null;
    });

    this.followUps = computed(() => {
      if (this.isLoading()) {
        return [];
      }
      const messages = this.conversation()?.messages ?? [];
      const last = messages[messages.length - 1];
      return last && last.role === 'assistant' && !last.error ? this.followUpsFor(last) : [];
    });

    effect(() => {
      const id = this.conversationId();
      if (id) {
        this.chatService.ensureLoaded(id);
      }
    });

    // Opening a different conversation always starts at its latest message.
    effect(() => {
      this.conversationId();
      this.stickToBottom.set(true);
    });

    effect(() => {
      this.conversation();
      this.isLoading();
      queueMicrotask(() => {
        if (this.stickToBottom()) {
          this.scrollToBottom(false);
        }
      });
    });

    effect(() => {
      const input = this.messageInput();
      if (input && this.refocusInput) {
        this.refocusInput = false;
        input.nativeElement.focus();
      }
    });

    effect(() => {
      const loading = this.isLoading();
      if (loading && !this.wasLoading) {
        this.announcement.set('The assistant is replying.');
      } else if (!loading && this.wasLoading) {
        const messages = untracked(() => this.conversation()?.messages ?? []);
        const last = messages[messages.length - 1];
        this.announcement.set(
          last?.error
            ? 'The reply failed.'
            : last?.interrupted
              ? 'The reply was stopped.'
              : 'The reply is complete.',
        );
      }
      this.wasLoading = loading;
    });

    // Grow/shrink the textarea with its content, including when the draft is set from code
    // (a suggestion, an edit, or cleared after sending).
    effect(() => {
      this.draftText();
      queueMicrotask(() => this.resizeInput());
    });
  }

  /** The assistant message currently being streamed into, if any - its status line lives inside it. */
  protected streamingMessageId(): string | null {
    if (!this.isLoading()) {
      return null;
    }
    const messages = this.conversation()?.messages ?? [];
    const last = messages[messages.length - 1];
    return last && last.role === 'assistant' && last.content ? last.id : null;
  }

  /** The standalone "Thinking…" bubble: only until a reply has text to show the status inside. */
  protected showPendingBubble(): boolean {
    return this.isLoading() && this.streamingMessageId() === null;
  }

  protected isUserMessage(message: ChatMessage): boolean {
    return message.role === 'user';
  }

  protected isLastMessage(message: ChatMessage): boolean {
    const messages = this.conversation()?.messages ?? [];
    return messages[messages.length - 1]?.id === message.id;
  }

  /** Whether Retry / Regenerate may be offered for this conversation right now. */
  protected canResend(): boolean {
    const id = this.conversationId();
    return id ? this.chatService.canResend(id) : false;
  }

  protected retry(): void {
    const id = this.conversationId();
    if (id) {
      this.stickToBottom.set(true);
      this.chatService.resend(id);
    }
  }

  /**
   * One-click replies for what the assistant just showed. A relabel or review is only committed in
   * a *later* turn, so the proposal card is followed by an explicit yes/no; an inspection by
   * the usual next step (saying the label is wrong, which also saves it as a case).
   */
  private followUpsFor(message: ChatMessage): string[] {
    if (message.interrupted) {
      return ['Please continue from where you stopped.'];
    }
    const results = message.toolResults ?? [];
    if (results.length === 0 && this.asksForConfirmation(message.content)) {
      return ['Yes', 'No'];
    }
    for (const { name, result } of [...results].reverse()) {
      switch (name) {
        case 'create_case': {
          // A case with no defect label has nothing to correct, so a relabel would only be refused.
          return result.defect ? [`The label on case ${result.case_number} is wrong - relabel it.`] : [];
        }
        case 'inspect_image':
        case 'reinspect_image':
          return [
            ...(result.explain_available ? ['Explain this result.'] : []),
            ...(result.defect ? ['The label is right.'] : []),
            result.defect
              ? "The model's defect label is wrong - I'll tell you the right one."
              : "The region is wrong - I'll tell you the right one.",
            'Open a case for this inspection.',
            result.review_required
              ? 'Why was this flagged for review?'
              : 'What else did the model consider?',
          ];
        case 'explain_inspection':
          return result.agrees_with_model === false
            ? ["The model's defect label is wrong - I'll tell you the right one.", 'The label is right.']
            : [];
        case 'relabel_case':
          return ['Yes, confirm the relabel.', "No, cancel it - keep the model's label."];
        case 'confirm_relabel':
          return [result.stage === 'region' ? 'Is the region model drifting?' : 'Is the defect model drifting?'];
        case 'review_case':
          return ['Yes, confirm the review.', 'No, cancel it.'];
        default:
          return [];
      }
    }
    return [];
  }

  /**
   * True when the reply ends by asking the user to confirm or choose yes/no, so a Yes / No chip is
   * worth offering. Looks only at the last paragraph: an answer that merely contains "confirm"
   * earlier on is not a question. Cards that ask for confirmation (a relabel proposal) have their
   * own chips, so this is only for a plain-text ask.
   */
  private asksForConfirmation(content: string): boolean {
    const last = content.trim().split(/\n\s*\n/).pop() ?? '';
    if (last.length === 0 || last.length > 400) {
      return false;
    }
    const ask =
      /\b(please confirm|confirm (that|if|whether|this|the)|would you like|do you want|shall i|should i|want me to|ok to proceed|proceed\b.*\?)/i;
    return ask.test(last) && (/[?]\s*$/.test(last) || /please confirm/i.test(last));
  }

  /** Thumbs up/down on a reply; choosing the one already chosen takes it back. */
  protected rate(message: ChatMessage, value: 'up' | 'down'): void {
    const id = this.conversationId();
    if (id) {
      void this.chatService.rate(id, message.id, message.feedback === value ? null : value);
    }
  }

  protected sendFollowUp(text: string): void {
    this.draftText.set(text);
    this.send();
  }

  protected async copyMessage(message: ChatMessage): Promise<void> {
    try {
      await navigator.clipboard.writeText(message.content);
    } catch {
      return; // clipboard blocked (insecure origin, permission) - nothing to confirm
    }
    this.copiedMessageId.set(message.id);
    setTimeout(() => {
      if (this.copiedMessageId() === message.id) {
        this.copiedMessageId.set(null);
      }
    }, COPIED_FEEDBACK_MS);
  }

  /** Puts a sent message back in the box to change and send as a new message. The earlier turn
   * stays in the thread: the backend replays history from its own records, so dropping it here
   * would only hide context the model still has. */
  protected editMessage(message: ChatMessage): void {
    this.draftText.set(message.content);
    this.messageInput()?.nativeElement.focus();
  }

  protected onScroll(): void {
    const el = this.scrollContainer()?.nativeElement;
    if (!el) {
      return;
    }
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    this.stickToBottom.set(distance < STICK_TO_BOTTOM_PX);
  }

  protected jumpToLatest(): void {
    this.stickToBottom.set(true);
    this.scrollToBottom(true);
  }

  private scrollToBottom(smooth: boolean): void {
    const el = this.scrollContainer()?.nativeElement;
    if (!el) {
      return;
    }
    if (smooth && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    } else {
      el.scrollTop = el.scrollHeight;
    }
  }

  protected resizeInput(): void {
    const el = this.messageInput()?.nativeElement;
    if (!el) {
      return;
    }
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT_PX)}px`;
  }

  protected stop(): void {
    const id = this.conversationId();
    if (id) {
      this.chatService.stop(id);
    }
  }

  /** The one attach button: images and inspection XML go to their own lists. */
  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.addAttachments(Array.from(input.files ?? []));
    input.value = '';
  }

  onXmlFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.addXmlFiles(Array.from(input.files ?? []));
    input.value = '';
  }

  removePendingXml(index: number): void {
    this.pendingXmlFiles.update((files) => files.filter((_, i) => i !== index));
  }

  onDragEnter(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth++;
    this.isDraggingOver.set(true);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault(); // required for a drop event to fire at all
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) {
      this.isDraggingOver.set(false);
    }
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth = 0;
    this.isDraggingOver.set(false);
    this.addAttachments(Array.from(event.dataTransfer?.files ?? []));
  }

  /** Pasting a screenshot or copied image attaches it; pasted text is left to the textarea. */
  onPaste(event: ClipboardEvent): void {
    const images = Array.from(event.clipboardData?.files ?? []).filter((file) =>
      file.type.startsWith('image/'),
    );
    if (images.length > 0) {
      event.preventDefault();
      this.addFiles(images);
    }
  }

  private addAttachments(files: File[]): void {
    const accepted: File[] = [];
    const refused: string[] = [];
    for (const file of files) {
      if (!file.type.startsWith('image/') && !isXml(file)) {
        refused.push(`${file.name} is not an image or an XML file`);
      } else if (file.size > MAX_ATTACHMENT_BYTES) {
        refused.push(`${file.name} is larger than 20 MB`);
      } else {
        accepted.push(file);
      }
    }
    this.addFiles(accepted);
    this.addXmlFiles(accepted);
    this.showNotice(refused.length ? `Not attached: ${refused.join('; ')}.` : null);
  }

  private showNotice(text: string | null): void {
    if (this.noticeTimer) {
      clearTimeout(this.noticeTimer);
      this.noticeTimer = null;
    }
    this.attachmentNotice.set(text);
    if (text) {
      this.noticeTimer = setTimeout(() => this.attachmentNotice.set(null), NOTICE_MS);
    }
  }

  protected dismissNotice(): void {
    this.showNotice(null);
  }

  private addFiles(files: File[]): void {
    const additions = files
      .filter((file) => file.type.startsWith('image/'))
      .map((file) => ({ file, previewUrl: URL.createObjectURL(file) }));
    this.pendingImages.update((images) => [...images, ...additions]);
  }

  private addXmlFiles(files: File[]): void {
    const additions = files.filter(isXml).map((file) => ({ file }));
    this.pendingXmlFiles.update((existing) => [...existing, ...additions]);
  }

  useSuggestion(suggestion: Suggestion): void {
    this.draftText.set(suggestion.prompt);
    if (suggestion.needsAttachment) {
      this.fileInput?.nativeElement.click();
    } else if (suggestion.send) {
      this.send();
    } else {
      this.messageInput()?.nativeElement.focus();
    }
  }

  removePendingImage(index: number): void {
    this.pendingImages.update((images) => {
      URL.revokeObjectURL(images[index].previewUrl);
      return images.filter((_, i) => i !== index);
    });
  }

  send(): void {
    const text = this.draftText().trim();
    const images = this.pendingImages();
    const xmlFiles = this.pendingXmlFiles();
    if ((!text && images.length === 0 && xmlFiles.length === 0) || this.isLoading()) {
      return;
    }

    this.showNotice(null);
    this.stickToBottom.set(true);
    this.refocusInput = true;
    this.messageInput()?.nativeElement.focus();

    const conversationId = this.chatService.send(
      this.conversationId(),
      text,
      images.map((image) => image.file),
      xmlFiles.map((xml) => xml.file),
    );

    this.draftText.set('');
    for (const image of images) {
      URL.revokeObjectURL(image.previewUrl); // the sent message made its own object URLs
    }
    this.pendingImages.set([]);
    this.pendingXmlFiles.set([]);
    if (this.fileInput) {
      this.fileInput.nativeElement.value = '';
    }

    if (this.conversationId() !== conversationId) {
      this.router.navigate(['/c', conversationId]);
    }
  }

  onTextareaKeydown(event: KeyboardEvent): void {
    // Enter that confirms an IME candidate (Japanese, Chinese, Korean) must not send the message.
    if (event.isComposing || event.keyCode === 229) {
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }
}
