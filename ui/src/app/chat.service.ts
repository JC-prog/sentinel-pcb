import { Inject, Injectable, Signal, computed, effect, signal } from '@angular/core';
import { environment } from '../environments/environment';
import { AuthService } from './auth.service';
import { CHAT_RESPONDER, ChatResponder } from './chat-responder';
import { ChatMessage, Conversation, MessageRole, PersistedConversation } from './models/chat.models';

const STORAGE_KEY_PREFIX = 'sentinel-chat.conversations';
const MAX_TITLE_LENGTH = 48;

interface ServerMessage {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
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
  return { id: message.id, role: message.role, content: message.content, createdAt: Date.parse(message.created_at) };
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
  private readonly hydratedConversationIds = new Set<string>();
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

  /** Fetches a conversation's full message history from the server the first time it's opened,
   * if this browser doesn't already have messages for it locally - covers reopening a
   * conversation after a reload or from a different device. Deliberately skipped once local
   * messages exist (fresh sends, or an already-hydrated conversation) so a fetch racing an
   * in-flight streaming reply can never overwrite it - see ui/src/app/chat/chat.ts's usage. */
  ensureLoaded(id: string): void {
    if (this.hydratedConversationIds.has(id)) {
      return;
    }
    this.hydratedConversationIds.add(id);
    const existing = this.conversations().find((c) => c.id === id);
    if (existing && existing.messages.length > 0) {
      return;
    }
    void this.hydrateConversation(id);
  }

  private async hydrateConversation(id: string): Promise<void> {
    try {
      const response = await this.authService.fetchWithAuth(`${environment.apiBaseUrl}/api/conversations/${id}`);
      if (!response.ok) {
        return; // 404 - a brand-new conversation that hasn't been sent yet.
      }
      const detail: ServerConversationDetail = await response.json();
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
  send(conversationId: string | null, text: string, images: File[]): string {
    const now = this.now();
    const imageUrls = images.map((file) => URL.createObjectURL(file));
    const userMessage: ChatMessage = {
      id: newId(),
      role: 'user',
      content: text,
      imageUrls: imageUrls.length ? imageUrls : undefined,
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
    this.awaitReply(id, text, images);
    return id;
  }

  /**
   * The responder streams the reply as zero or more chunks. The first chunk creates the
   * assistant message; every chunk after that appends to it, so the UI renders the reply
   * arriving incrementally instead of waiting for the whole thing.
   */
  private awaitReply(conversationId: string, text: string, images: File[]): void {
    this.loadingIds.update((ids) => new Set(ids).add(conversationId));
    let assistantMessageId: string | null = null;

    const appendChunk = (chunk: string): void => {
      const now = this.now();
      this.conversations.update((all) =>
        all.map((c) => {
          if (c.id !== conversationId) {
            return c;
          }
          if (assistantMessageId === null) {
            assistantMessageId = newId();
            const assistantMessage: ChatMessage = {
              id: assistantMessageId,
              role: 'assistant',
              content: chunk,
              createdAt: now,
            };
            return { ...c, messages: [...c.messages, assistantMessage], updatedAt: now };
          }
          return {
            ...c,
            messages: c.messages.map((m) =>
              m.id === assistantMessageId ? { ...m, content: m.content + chunk } : m,
            ),
            updatedAt: now,
          };
        }),
      );
    };

    const finish = (): void => {
      this.loadingIds.update((ids) => {
        const next = new Set(ids);
        next.delete(conversationId);
        return next;
      });
      this.persist();
    };

    this.responder.respond(conversationId, text, images).subscribe({
      next: appendChunk,
      error: () => {
        appendChunk('Sorry, something went wrong reaching the assistant.');
        finish();
      },
      complete: finish,
    });
  }

  private persist(): void {
    saveToStorage(this.storageKey, this.conversations());
  }
}
