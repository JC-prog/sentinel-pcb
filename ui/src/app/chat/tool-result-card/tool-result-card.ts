import { Component, computed, input } from '@angular/core';
import {
  InspectionResult,
  RelabelDone,
  RelabelProposal,
  ReviewDone,
  ReviewProposal,
  ToolResult,
} from '../models/chat.models';

/**
 * The structured result of a tool, shown to the user as a card: an inspection (case number,
 * verdict, what each classifier found and how sure it was, why it needs review) a relabel or a case
 * review (the proposal awaiting their confirmation, or what was recorded). The model describes the same
 * thing in words; the card is the exact numbers, straight from the tool, never the model's retelling.
 */
@Component({
  selector: 'app-tool-result-card',
  templateUrl: './tool-result-card.html',
})
export class ToolResultCard {
  readonly toolResult = input.required<ToolResult>();

  protected readonly inspection = computed<InspectionResult | null>(() => {
    const t = this.toolResult();
    return t.name === 'inspect_image' ? t.result : null;
  });

  protected readonly proposal = computed<RelabelProposal | null>(() => {
    const t = this.toolResult();
    return t.name === 'relabel_case' ? t.result : null;
  });

  protected readonly relabelled = computed<RelabelDone | null>(() => {
    const t = this.toolResult();
    return t.name === 'confirm_relabel' ? t.result : null;
  });

  protected readonly reviewProposal = computed<ReviewProposal | null>(() => {
    const t = this.toolResult();
    return t.name === 'review_case' ? t.result : null;
  });

  protected readonly reviewed = computed<ReviewDone | null>(() => {
    const t = this.toolResult();
    return t.name === 'confirm_review' ? t.result : null;
  });

  /** What a decision means, for the card's wording. */
  protected meaning(decision: 'approve' | 'override'): string {
    return decision === 'approve' ? 'defect confirmed' : 'false positive';
  }

  /** 0.8731 -> "87%". */
  protected percent(value: number): string {
    return `${Math.round(value * 100)}%`;
  }
}
