import { Inject, Injectable, Signal, computed, signal } from '@angular/core';
import { CHAT_RESPONDER, ChatResponder } from './chat-responder';
import { ChatMessage, Conversation, PersistedConversation } from './models/chat.models';

const STORAGE_KEY = 'sentinel-chat.conversations';
const MAX_TITLE_LENGTH = 48;

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

function loadFromStorage(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed: PersistedConversation[] = JSON.parse(raw);
    return parsed.map((conversation) => ({ ...conversation, messages: [...conversation.messages] }));
  } catch {
    return [];
  }
}

function saveToStorage(conversations: Conversation[]): void {
  const persisted: PersistedConversation[] = conversations.map((conversation) => ({
    ...conversation,
    messages: conversation.messages.map(({ imageUrls: _imageUrls, ...rest }) => rest),
  }));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
  } catch {
    // Storage full or unavailable (private browsing) - history just won't persist this session.
  }
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly conversations = signal<Conversation[]>(loadFromStorage());
  private readonly loadingIds = signal<ReadonlySet<string>>(new Set());
  private lastTimestamp = 0;

  constructor(@Inject(CHAT_RESPONDER) private readonly responder: ChatResponder) {}

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
    saveToStorage(this.conversations());
  }
}
