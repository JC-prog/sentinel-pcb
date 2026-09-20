export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  /** Object URLs for images attached to this message. In-memory only - not persisted, since
   * blob: URLs don't survive a page reload and there's no backend yet to upload the files to. */
  imageUrls?: string[];
  createdAt: number;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

/** Shape actually written to localStorage: same as Conversation, but with each message's
 * imageUrls stripped (see ChatMessage.imageUrls). */
export type PersistedConversation = Omit<Conversation, 'messages'> & {
  messages: Omit<ChatMessage, 'imageUrls'>[];
};
