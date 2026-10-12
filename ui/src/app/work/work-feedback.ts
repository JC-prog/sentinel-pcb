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

export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

/** The run in one sentence for the Model feedback tab: how the defect model did, and what that
 * means for what to do next. */
export interface Verdict {
  tone: Tone;
  headline: string;
  advice: string;
}

export function feedbackVerdict(
  totals: { decided: number; confirmed: number; relabelled: number },
  pending: number,
): Verdict {
  const answered = totals.confirmed + totals.relabelled;
  const more = pending > 0 ? ' More flagged samples are still to review, so this may change.' : '';
  if (totals.decided === 0) {
    return {
      tone: 'neutral',
      headline: 'Nothing reviewed yet',
      advice: 'Review the flagged samples to see how the model did.',
    };
  }
  if (answered === 0) {
    return {
      tone: 'neutral',
      headline: 'The defect model did not classify any reviewed sample',
      advice:
        'The region model was not sure enough for a defect model to run. Your labels can still be sent for retraining.' +
        more,
    };
  }
  const rate = totals.relabelled / answered;
  const percentWrong = Math.round(rate * 100);
  if (totals.relabelled === 0) {
    return {
      tone: 'good',
      headline: `The defect model was right on all ${answered} sample${answered === 1 ? '' : 's'} it classified`,
      advice: 'Nothing to correct from this run.' + more,
    };
  }
  const headline = `The defect model was wrong on ${totals.relabelled} of ${answered} sample${answered === 1 ? '' : 's'} (${percentWrong}%)`;
  if (rate >= 0.25) {
    return {
      tone: 'bad',
      headline,
      advice: 'That is a lot. Send the corrections so the model can learn from them.' + more,
    };
  }
  if (rate >= 0.1) {
    return {
      tone: 'warn',
      headline,
      advice: 'Some mistakes. Worth sending the corrections.' + more,
    };
  }
  return {
    tone: 'good',
    headline,
    advice: 'Mostly right. Send the corrections if you want it to learn from the misses.' + more,
  };
}

/** How one model is doing, from its error rate on the reviewed samples. A handful of samples is
 * too few to call - heuristics, not rules. */
export function modelStatus(rate: number | null, decided: number): { tone: Tone; label: string } {
  if (rate === null) {
    return { tone: 'neutral', label: 'Not classified' };
  }
  if (decided < 5) {
    return { tone: 'neutral', label: 'Too few samples to judge' };
  }
  if (rate >= 0.25) {
    return { tone: 'bad', label: 'Needs attention' };
  }
  if (rate >= 0.1) {
    return { tone: 'warn', label: 'Keep an eye on it' };
  }
  return { tone: 'good', label: 'Looks healthy' };
}

/** The same colour for a label's bar segment and its legend swatch. */
export function mixColor(index: number): string {
  return `hsl(${index * 57 + 200} 65% 55%)`;
}

export function toneChipClass(tone: Tone): string {
  switch (tone) {
    case 'good':
      return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200';
    case 'warn':
      return 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200';
    case 'bad':
      return 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200';
    default:
      return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
  }
}

/** The chip colour for where a correction stands on its way to retraining. */
export function correctionStatusClass(correction: WorkflowCorrection): string {
  if (!correction.queueable && !correction.queued) {
    return toneChipClass('neutral');
  }
  switch (correction.ticket_status) {
    case 'open':
      return 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200';
    case 'acknowledged':
      return toneChipClass('warn');
    case 'resolved':
      return toneChipClass('good');
    default:
      return toneChipClass('neutral');
  }
}

/** The small dot that carries a tone beside a read-out (the text stays in ink). */
export function toneDotClass(tone: Tone): string {
  switch (tone) {
    case 'good':
      return 'viz-good';
    case 'warn':
      return 'viz-warn';
    case 'bad':
      return 'viz-bad';
    default:
      return 'viz-none';
  }
}
