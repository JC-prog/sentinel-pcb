export type MessageRole = 'user' | 'assistant';

/** One label and how likely the classifier thought it was. */
export interface ScoreEntry {
  label: string;
  score: number;
}

/** One classifier's answer inside an inspection: which model and weights, what it said. */
export interface StageResult {
  model: string;
  model_version: string | null;
  label: string;
  confidence: number;
  top_scores: ScoreEntry[];
}

/** What the inspect_image tool returns (app/chat/agents/inspection_agent/tool.py). It saves
 * nothing: a case is only created when the user asks for one (the create_case tool), so there is no
 * case number here. */
export interface InspectionResult {
  case_created: false;
  verdict: 'accepted' | 'review_required';
  review_required: boolean;
  review_reasons: string[];
  inspection_summary: string | null;
  region: StageResult | null;
  /** Null when the region was too uncertain for a defect model to run. */
  defect: StageResult | null;
  measurement_validation: { valid: boolean; issues: string[] } | null;
}

/** relabel_case: a correction waiting for the user's confirmation - nothing is saved yet. */
export interface RelabelProposal {
  status: 'awaiting_confirmation';
  case_number: string;
  model: string;
  model_label: string;
  proposed_label: string;
  reason: string;
}

/** confirm_relabel: the correction was recorded and a retraining ticket queued. */
export interface RelabelDone {
  status: 'relabelled';
  case_number: string;
  model: string;
  model_label: string;
  corrected_label: string;
  ticket_id: string;
}

/** review_case: an approve/override waiting for the user's confirmation - nothing is saved yet. */
export interface ReviewProposal {
  status: 'awaiting_confirmation';
  case_number: string;
  model_label: string | null;
  decision: 'approve' | 'override';
  note: string | null;
}

/** confirm_review: the case was resolved. */
export interface ReviewDone {
  status: 'reviewed';
  case_number: string;
  model_label: string | null;
  decision: 'approve' | 'override';
  case_status: 'approved' | 'overridden';
  note: string | null;
}

/** A tool's structured result, shown as a card on the assistant message that follows it. Mirrors
 * the backend's `event: tool_result` SSE frame (app/chat/services/streaming.py). */
export type ToolResult =
  | { name: 'inspect_image'; result: InspectionResult }
  | { name: 'relabel_case'; result: RelabelProposal }
  | { name: 'confirm_relabel'; result: RelabelDone }
  | { name: 'review_case'; result: ReviewProposal }
  | { name: 'confirm_review'; result: ReviewDone };

export const TOOL_RESULT_NAMES: readonly ToolResult['name'][] = [
  'inspect_image',
  'relabel_case',
  'confirm_relabel',
  'review_case',
  'confirm_review',
];

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  /** Object URLs for images attached to this message. In-memory only - not persisted, since
   * blob: URLs don't survive a page reload and there's no backend yet to upload the files to. */
  imageUrls?: string[];
  /** Cards for the tools that ran while this reply was produced (inspection result, relabel). */
  toolResults?: ToolResult[];
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
