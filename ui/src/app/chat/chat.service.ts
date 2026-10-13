import { Inject, Injectable, Signal, computed, effect, signal } from '@angular/core';
import { environment } from '../../environments/environment';
import { AuthService } from '../auth.service';
import { CHAT_RESPONDER, ChatResponder, ChatResponderError } from './chat-responder';
import {
  ChatMessage,
  Conversation,
  MessageRole,
  PersistedConversation,
  TOOL_RESULT_NAMES,
  ToolResult,
} from './models/chat.models';

const STORAGE_KEY_PREFIX = 'sentinel-chat.conversations';
const MAX_TITLE_LENGTH = 48;

interface ServerMessage {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  tool_results?: { name: string; result: unknown }[];
  interrupted?: boolean;
}

interface ServerConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface ServerConversationDetail extends ServerConversationSummary {
  messages: ServerMessage[];
}

function newId(): string {
  return crypto.randomUUID();
}

function toTitle(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, ' ');
  if (trimmed.length <= MAX_TITLE_LENGTH) {
    return trimmed || 'New chat';
  }
  return trimmed.slice(0, MAX_TITLE_LENGTH - 1) + '…';
}

/** Not the fixed constant it used to be - see ChatService's per-user storage key comment below. */
function storageKeyFor(userId: string | null): string {
  return `${STORAGE_KEY_PREFIX}.${userId ?? 'anon'}`;
}

function loadFromStorage(key: string): Conversation[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return [];
    }
    const parsed: PersistedConversation[] = JSON.parse(raw);
    return parsed.map((conversation) => ({ ...conversation, messages: [...conversation.messages] }));
  } catch {
    return [];
  }
}

function saveToStorage(key: string, conversations: Conversation[]): void {
  const persisted: PersistedConversation[] = conversations.map((conversation) => ({
    ...conversation,
    messages: conversation.messages.map(({ imageUrls: _imageUrls, ...rest }) => rest),
  }));
  try {
    localStorage.setItem(key, JSON.stringify(persisted));
  } catch {
    // Storage full or unavailable (private browsing) - history just won't persist this session.
  }
}

function fromServerMessage(message: ServerMessage): ChatMessage {
  const toolResults = (message.tool_results ?? []).filter((entry) =>
    TOOL_RESULT_NAMES.includes(entry.name as ToolResult['name']),
  ) as ToolResult[];
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    ...(toolResults.length ? { toolResults } : {}),
    ...(message.interrupted ? { interrupted: true } : {}),
    createdAt: Date.parse(message.created_at),
  };
}

function fromServerSummary(summary: ServerConversationSummary): Conversation {
  return {
    id: summary.id,
    title: summary.title,
    messages: [],
    createdAt: Date.parse(summary.created_at),
    updatedAt: Date.parse(summary.updated_at),
  };
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly conversations = signal<Conversation[]>([]);
  private readonly loadingIds = signal<ReadonlySet<string>>(new Set());
  /** conversationId -> the label of whichever tool call is currently in flight (e.g. "Case lookup"), or absent when none is - a transient UI progress indicator only, never persisted to
   * history. Cleared as soon as real reply text starts arriving, or the turn ends either way. */
  private readonly toolCallLabels = signal<ReadonlyMap<string, string>>(new Map());
  private readonly hydratedConversationIds = new Set<string>();
  /** conversationId -> cancels its in-flight reply (see stop()). */
  private readonly activeReplies = new Map<string, () => void>();
  /** conversationId -> what the last send carried, so Retry/Regenerate can resend the files too.
   * In memory only (File objects don't survive a reload). */
  private readonly lastRequests = new Map<
    string,
    { text: string; images: File[]; xmlFiles: File[] }
  >();
  private lastTimestamp = 0;
  private storageKey = storageKeyFor(null);

  constructor(
    @Inject(CHAT_RESPONDER) private readonly responder: ChatResponder,
    private readonly authService: AuthService,
  ) {
    // Sidebar (and so ChatService) is constructed app-wide, before authGuard has resolved who's
    // logged in - so the storage key can't just be computed once at construction. This reacts
    // once currentUser settles (and again on every login/logout after that), re-keying
    // localStorage per user so two accounts on the same browser never see each other's chats -
    // see ui/src/app/chat.service.spec.ts for the isolation this fixes.
    effect(() => {
      const user = this.authService.currentUser();
      if (user === undefined) {
        return;
      }
      this.switchUser(user?.id ?? null);
    });
  }

  private switchUser(userId: string | null): void {
    this.storageKey = storageKeyFor(userId);
    this.hydratedConversationIds.clear();
    this.conversations.set(loadFromStorage(this.storageKey));
    if (userId) {
      void this.hydrateList();
    }
  }

  /** Adds conversations the server knows about but this browser doesn't yet (e.g. started on
   * another device) - never overwrites a locally-known conversation, so an optimistic send that
   * hasn't round-tripped yet can't be clobbered by a list call that raced ahead of it. */
  private async hydrateList(): Promise<void> {
    try {
      const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}/api/conversations`);
      if (!response.ok) {
        return;
      }
      const summaries: ServerConversationSummary[] = await response.json();
      this.conversations.update((all) => {
        const knownIds = new Set(all.map((c) => c.id));
        const additions = summaries.filter((s) => !knownIds.has(s.id)).map(fromServerSummary);
        return additions.length ? [...all, ...additions] : all;
      });
      this.persist();
    } catch {
      // Offline/unreachable - the locally cached list is still shown.
    }
  }

  /** Fetches a conversation's full message history from the server the first time it's opened in
   * this session - covers reopening a conversation after a reload, and one continued on another
   * device. The server's copy only replaces the local one when it has more messages (see
   * hydrateConversation), and never while a reply is streaming into it - see
   * ui/src/app/chat/chat.ts's usage. */
  ensureLoaded(id: string): void {
    if (this.hydratedConversationIds.has(id)) {
      return;
    }
    this.hydratedConversationIds.add(id);
    void this.hydrateConversation(id);
  }

  private async hydrateConversation(id: string): Promise<void> {
    try {
      const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}/api/conversations/${id}`);
      if (!response.ok) {
        return; // 404 - a brand-new conversation that hasn't been sent yet.
      }
      const detail: ServerConversationDetail = await response.json();
      const current = this.conversations().find((c) => c.id === id);
      // A reply is streaming into it, or this browser already has everything the server has
      // (it also has local-only state: attachments' thumbnails, a failed reply to retry).
      if (this.loadingIds().has(id) || (current && current.messages.length >= detail.messages.length)) {
        return;
      }
      const hydrated: Conversation = {
        id: detail.id,
        title: detail.title,
        messages: detail.messages.map(fromServerMessage),
        createdAt: Date.parse(detail.created_at),
        updatedAt: Date.parse(detail.updated_at),
      };
      this.conversations.update((all) => [...all.filter((c) => c.id !== id), hydrated]);
      this.persist();
    } catch {
      // Offline/unreachable - keep whatever's cached locally, if anything.
    }
  }

  /** Monotonically increasing, even across calls made within the same millisecond - keeps
   * conversation/message ordering deterministic regardless of clock resolution. */
  private now(): number {
    this.lastTimestamp = Math.max(Date.now(), this.lastTimestamp + 1);
    return this.lastTimestamp;
  }

  list(): Signal<Conversation[]> {
    return computed(() => [...this.conversations()].sort((a, b) => b.updatedAt - a.updatedAt));
  }

  get(id: string): Signal<Conversation | undefined> {
    return computed(() => this.conversations().find((c) => c.id === id));
  }

  isLoading(id: string): Signal<boolean> {
    return computed(() => this.loadingIds().has(id));
  }

  /** The in-flight tool call's display label for this conversation (e.g. "Explainability Agent"),
   * or null when no tool call is currently running - see isLoading() for the broader "still
   * waiting on a reply at all" signal, which stays true for longer than this does. */
  toolCallLabel(id: string): Signal<string | null> {
    return computed(() => this.toolCallLabels().get(id) ?? null);
  }

  /** Renames a conversation. Optimistic; put back if the server refuses. */
  rename(id: string, title: string): void {
    const next = title.trim().replace(/\s+/g, ' ').slice(0, 80);
    const previous = this.conversations().find((c) => c.id === id)?.title;
    if (!next || previous === undefined || next === previous) {
      return;
    }
    const setTitle = (value: string): void => {
      this.conversations.update((all) => all.map((c) => (c.id === id ? { ...c, title: value } : c)));
      this.persist();
    };
    setTitle(next);
    void this.authService
      .fetchWithAuth(`${environment.apiBaseUrl}/api/conversations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: next }),
      })
      .then((response) => {
        if (!response.ok && response.status !== 404) {
          setTitle(previous);
        }
      })
      .catch(() => setTitle(previous));
  }

  delete(id: string): void {
    this.conversations.update((all) => all.filter((c) => c.id !== id));
    this.persist();
    void this.authService
      .fetchWithAuth(`${environment.apiBaseUrl}/api/conversations/${id}`, { method: 'DELETE' })
      .catch(() => {
        // Best-effort - a failed delete just means it reappears next time the list is hydrated.
      });
  }

  /**
   * Sends a user message, creating a new conversation first if conversationId is null.
   * Returns the id of the conversation the message was added to.
   */
  send(conversationId: string | null, text: string, images: File[], xmlFiles: File[] = []): string {
    const now = this.now();
    const imageUrls = images.map((file) => URL.createObjectURL(file));
    const userMessage: ChatMessage = {
      id: newId(),
      role: 'user',
      content: text,
      imageUrls: imageUrls.length ? imageUrls : undefined,
      attachmentCount: images.length + xmlFiles.length || undefined,
      createdAt: now,
    };

    const id = conversationId ?? newId();
    this.hydratedConversationIds.add(id);
    this.conversations.update((all) => {
      const existing = all.find((c) => c.id === id);
      if (existing) {
        return all.map((c) =>
          c.id === id ? { ...c, messages: [...c.messages, userMessage], updatedAt: now } : c,
        );
      }
      const created: Conversation = {
        id,
        title: toTitle(text || 'New chat'),
        messages: [userMessage],
        createdAt: now,
        updatedAt: now,
      };
      return [...all, created];
    });
    this.persist();
    this.lastRequests.set(id, { text, images, xmlFiles });
    this.awaitReply(id, text, images, xmlFiles);
    return id;
  }

  /** Whether the conversation's last user message can be sent again (retry / regenerate): nothing
   * is streaming, and any files it carried are still in memory - they are gone after a reload, and
   * silently resending without them would change what the assistant is asked. */
  canResend(id: string): boolean {
    if (this.loadingIds().has(id)) {
      return false;
    }
    const messages = this.conversations().find((c) => c.id === id)?.messages ?? [];
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    if (!lastUser) {
      return false;
    }
    return !lastUser.attachmentCount || this.lastRequests.has(id);
  }

  /** Drops everything after the last user message and asks again - used for Retry after a failed
   * reply and for Regenerate. The user message is not added a second time here, though the
   * backend does see it again. */
  resend(id: string): void {
    if (!this.canResend(id)) {
      return;
    }
    const messages = this.conversations().find((c) => c.id === id)?.messages ?? [];
    let lastUserIndex = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        lastUserIndex = i;
        break;
      }
    }
    const text = messages[lastUserIndex].content;
    const request = this.lastRequests.get(id);
    this.conversations.update((all) =>
      all.map((c) =>
        c.id === id ? { ...c, messages: c.messages.slice(0, lastUserIndex + 1), updatedAt: this.now() } : c,
      ),
    );
    this.persist();
    this.awaitReply(id, text, request?.images ?? [], request?.xmlFiles ?? []);
  }

  /**
   * The responder streams the reply as zero or more chunks. The first chunk creates the
   * assistant message; every chunk after that appends to it, so the UI renders the reply
   * arriving incrementally instead of waiting for the whole thing.
   */
  private awaitReply(conversationId: string, text: string, images: File[], xmlFiles: File[]): void {
    this.loadingIds.update((ids) => new Set(ids).add(conversationId));
    let assistantMessageId: string | null = null;

    // The first change (a text chunk or a tool-result card, whichever comes first - a tool's card
    // arrives before the model's words about it) creates the assistant message; later ones amend it.
    const updateAssistant = (change: (message: ChatMessage) => ChatMessage): void => {
      const now = this.now();
      this.conversations.update((all) =>
        all.map((c) => {
          if (c.id !== conversationId) {
            return c;
          }
          if (assistantMessageId === null) {
            assistantMessageId = newId();
            const assistantMessage = change({
              id: assistantMessageId,
              role: 'assistant',
              content: '',
              createdAt: now,
            });
            return { ...c, messages: [...c.messages, assistantMessage], updatedAt: now };
          }
          return {
            ...c,
            messages: c.messages.map((m) => (m.id === assistantMessageId ? change(m) : m)),
            updatedAt: now,
          };
        }),
      );
    };

    const appendChunk = (chunk: string): void =>
      updateAssistant((m) => ({ ...m, content: m.content + chunk }));

    const appendToolResult = (toolResult: ToolResult): void =>
      updateAssistant((m) => ({ ...m, toolResults: [...(m.toolResults ?? []), toolResult] }));

    const setToolCallLabel = (label: string | null): void => {
      this.toolCallLabels.update((labels) => {
        const next = new Map(labels);
        if (label === null) {
          next.delete(conversationId);
        } else {
          next.set(conversationId, label);
        }
        return next;
      });
    };

    const finish = (): void => {
      this.activeReplies.delete(conversationId);
      this.loadingIds.update((ids) => {
        const next = new Set(ids);
        next.delete(conversationId);
        return next;
      });
      setToolCallLabel(null);
      this.persist();
    };

    const subscription = this.responder.respond(conversationId, text, images, xmlFiles).subscribe({
      next: (event) => {
        if (event.type === 'toolCall') {
          setToolCallLabel(event.label);
        } else if (event.type === 'toolResult') {
          setToolCallLabel(null);
          appendToolResult(event.toolResult);
        } else if (event.type === 'usage') {
          updateAssistant((m) => ({
            ...m,
            usage: { inputTokens: event.inputTokens, outputTokens: event.outputTokens },
          }));
        } else {
          setToolCallLabel(null);
          appendChunk(event.text);
        }
      },
      error: (error: unknown) => {
        const errorMessage = error instanceof ChatResponderError ? error.message : undefined;
        updateAssistant((m) => ({ ...m, error: true, errorMessage }));
        finish();
      },
      complete: finish,
    });

    // A synchronous responder has already finished by now - nothing left to stop.
    if (this.loadingIds().has(conversationId)) {
      this.activeReplies.set(conversationId, () => {
        subscription.unsubscribe(); // aborts the HTTP stream (HttpChatResponder's teardown)
        if (assistantMessageId !== null) {
          updateAssistant((m) => ({ ...m, interrupted: true }));
        }
        finish();
      });
    }
  }

  /** Stops a reply that is still streaming. Whatever text has arrived so far stays in the
   * conversation; the backend turn is aborted along with the stream. */
  stop(conversationId: string): void {
    this.activeReplies.get(conversationId)?.();
  }

  private persist(): void {
    saveToStorage(this.storageKey, this.conversations());
  }
}
