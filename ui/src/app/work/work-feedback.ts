import { WorkflowCorrection } from './models/orchestrator.models';

/** Pure helpers for the Retraining queue and Model feedback tabs. */

export const REGION_MODEL = 'pcb_region';

/** What a correction is called in the UI: the model it teaches. */
const MODEL_LABELS: Record<string, string> = {
  pcb_region: 'Region model',
  pcb_body_defect: 'Body defect model',
  pcb_lead_defect: 'Lead defect model',
  pcb_text_defect: 'Text defect model',
};

const MODEL_ORDER = ['pcb_region', 'pcb_body_defect', 'pcb_lead_defect', 'pcb_text_defect'];

/** One correction's identity: the sample AND the model, since a sample can carry two. */
export function correctionKey(correction: Pick<WorkflowCorrection, 'sample_id' | 'model_name'>): string {
  return `${correction.sample_id}|${correction.model_name ?? ''}`;
}

export function modelLabel(modelName: string | null): string {
  if (modelName === null) {
    return 'No model to route to';
  }
  return MODEL_LABELS[modelName] ?? modelName;
}

export interface LabelMix {
  label: string;
  count: number;
  share: number;
}

export interface CorrectionGroup {
  /** The model the corrections teach; null for samples with no model to route to. */
  modelName: string | null;
  title: string;
  corrections: WorkflowCorrection[];
  /** Not yet sent and sendable. */
  pending: WorkflowCorrection[];
  sent: number;
  inPlan: number;
  retrained: number;
  /** The labels being taught, most common first. */
  mix: LabelMix[];
  /** A plain-language hint about the batch, or null when it looks fine. */
  hint: string | null;
}

export function labelMix(corrections: readonly WorkflowCorrection[]): LabelMix[] {
  const counts = new Map<string, number>();
  for (const c of corrections) {
    counts.set(c.final_result, (counts.get(c.final_result) ?? 0) + 1);
  }
  const total = corrections.length;
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count, share: total === 0 ? 0 : (count / total) * 100 }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/** A hint when a batch is tiny or mostly one label - heuristics, not rules. */
export function batchHint(pending: number, mix: readonly LabelMix[]): string | null {
  if (pending === 0) {
    return null;
  }
  if (pending < 5) {
    return 'Small batch: a handful of samples teaches a model very little.';
  }
  const top = mix[0];
  if (top && top.share > 80) {
    return `Mostly one label (${top.label}): the retrained model may overfit to it.`;
  }
  return null;
}

/** The corrections grouped by the model they teach, region model first, the unroutable last. */
export function groupCorrections(corrections: readonly WorkflowCorrection[]): CorrectionGroup[] {
  const byModel = new Map<string | null, WorkflowCorrection[]>();
  for (const c of corrections) {
    const list = byModel.get(c.model_name) ?? [];
    list.push(c);
    byModel.set(c.model_name, list);
  }
  const rank = (name: string | null): number => {
    if (name === null) {
      return 1000;
    }
    const index = MODEL_ORDER.indexOf(name);
    return index === -1 ? 100 : index;
  };
  return [...byModel.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || (a ?? '').localeCompare(b ?? ''))
    .map(([modelName, items]) => {
      const pending = items.filter((c) => c.queueable && !c.queued);
      const mix = labelMix(pending.length > 0 ? pending : items);
      return {
        modelName,
        title: modelLabel(modelName),
        corrections: items,
        pending,
        sent: items.filter((c) => c.queued && c.ticket_status === 'open').length,
        inPlan: items.filter((c) => c.queued && c.ticket_status === 'acknowledged').length,
        retrained: items.filter((c) => c.queued && c.ticket_status === 'resolved').length,
        mix,
        hint: batchHint(pending.length, mix),
      };
    });
}
