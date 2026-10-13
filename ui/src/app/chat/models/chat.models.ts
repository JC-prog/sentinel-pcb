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
  /** Whether "explain this" can be asked for - the server has an explanation service to ask. */
  explain_available?: boolean;
  /** Which of several attached images this is (1-based), and how many were attached. */
  image_number?: number;
  images_attached?: number;
}

/** relabel_case: a correction waiting for the user's confirmation - nothing is saved yet. */
export interface RelabelProposal {
  status: 'awaiting_confirmation';
  /** Null when the inspection is not a case yet: confirming creates it. */
  case_number: string | null;
  model: string;
  model_label: string;
  proposed_label: string;
  reason: string;
}

/** confirm_relabel: the correction was recorded and a retraining ticket queued. */
export interface RelabelDone {
  status: 'relabelled';
  case_number: string;
  /** True when confirming the relabel is what saved the inspection as a new case. */
  case_created?: boolean;
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

/** create_case: the inspection was saved as a numbered Case. */
export interface CaseCreated {
  status: 'created';
  case_number: string;
  /** The case's status: 'accepted' or 'review_required'. */
  verdict: string;
  region: string | null;
  defect: string | null;
  defect_confidence: number | null;
}

/** A tool's structured result, shown as a card on the assistant message that follows it. Mirrors
 * the backend's `event: tool_result` SSE frame (app/chat/services/streaming.py). */
/** explain_inspection: Agent 2's advisory explanation of the inspection (it decides nothing). */
export interface Explanation {
  status: 'ok' | 'flagged';
  advisory: true;
  /** The class Agent 2 judged it to be, in the app's own names; null when it declined to decide. */
  label: string | null;
  confidence: number | null;
  summary: string | null;
  visual_evidence_cited: string | null;
  ipc_precedent_cited: string | null;
  /** The model's own call, in the same names, for the comparison. */
  model_label: string | null;
  agrees_with_model: boolean | null;
  guardrail: { status: string; hallucination_flagged: boolean; note: string | null };
  evidence: { visual: string; telemetry_recorded: boolean; precedents_found: boolean };
}

export type ToolResult =
  | { name: 'inspect_image'; result: InspectionResult }
  | { name: 'explain_inspection'; result: Explanation }
  | { name: 'create_case'; result: CaseCreated }
  | { name: 'relabel_case'; result: RelabelProposal }
  | { name: 'confirm_relabel'; result: RelabelDone }
  | { name: 'review_case'; result: ReviewProposal }
  | { name: 'confirm_review'; result: ReviewDone };

export const TOOL_RESULT_NAMES: readonly ToolResult['name'][] = [
  'inspect_image',
  'explain_inspection',
  'create_case',
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
  /** How many files (images + inspection XML) the user attached. Unlike imageUrls this survives a
   * reload, so the message can still say something was attached once the thumbnails are gone. */
  attachmentCount?: number;
  /** The reply failed before it finished; the UI offers a retry. Whatever text arrived is kept. */
  error?: boolean;
  /** Why it failed, in words safe to show (a session that expired, a rate limit, ...). */
  errorMessage?: string;
  /** The reply was stopped (by the user, or by a failure part-way): the text is what had streamed. */
  interrupted?: boolean;
  /** Tokens the model used for this reply, summed over every model call in the turn. Absent when
   * the provider did not report it. */
  usage?: { inputTokens: number; outputTokens: number };
  /** The server's id for this reply, once it has one - what a thumbs up/down is sent against. */
  serverId?: string;
  /** The user's rating of this reply. */
  feedback?: 'up' | 'down';
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
