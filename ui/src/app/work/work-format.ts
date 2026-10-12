import { WorkflowCorrection } from './models/orchestrator.models';

/** Display helpers for the Work tab. Pure, so they are tested without a component. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

/** 0.5 -> "50%"; "—" when there is nothing to measure. */
export function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

/** A defect label as the Explanation Review shows it: 'MissingPart_3' -> 'missing part'. Display
 * only - the same canonical form app/shared/modelops/run_drift.py's normalize_defect produces. */
export function labelText(label: string | null | undefined): string {
  if (!label) {
    return '—';
  }
  const spaced = label
    .trim()
    .split('_')[0]
    // Split before each capital that follows a lowercase letter or digit. A lookbehind for the
    // start of the string is not supported by Safari before 16.4.
    .replace(/([a-z0-9])(?=[A-Z])/g, '$1 ')
    .toLowerCase()
    .replace(/-/g, ' ')
    .split(/\s+/)
    .join(' ')
    .replace('insuffcient', 'insufficient');
  return spaced === 'golden' ? 'no defect' : spaced;
}

/** The Explanation Review's own names for how a final decision was made. */
export function sourceLabel(source: string | null | undefined): string {
  const labels: Record<string, string> = { MACHINE: 'Accept Machine', AI: 'Accept AI', MANUAL: 'Manual' };
  return source ? (labels[source] ?? source) : '—';
}

/** What the Review Console calls "Machine" for a sample: the defect model's prediction, or - when
 * no defect model ran - the AOI machine's own defect call. Same value on both screens. */
export function machineSaid(correction: WorkflowCorrection): string {
  return labelText(correction.agent1_label ?? correction.machine_label);
}

/** Why a sample is on the list when the defect model never answered: the region model (which only
 * says which part area it is looking at, not a defect) was not confident enough to route it. */
export function modelNote(correction: WorkflowCorrection): string {
  if (correction.outcome !== 'no_prediction') {
    return '';
  }
  const guess = correction.region_guess;
  if (!guess) {
    return 'The defect model did not run.';
  }
  const confidence = guess.confidence === null ? '' : ` ${Math.round(guess.confidence * 100)}%`;
  return `The defect model did not run: the region model was unsure (${guess.label} area${confidence}).`;
}

/** Where a correction stands on its way to retraining. */
export function trainingStatus(correction: WorkflowCorrection): string {
  if (!correction.queueable && !correction.queued) {
    return "Can't send: no defect model to route it to";
  }
  switch (correction.ticket_status) {
    case 'open':
      return 'Sent, waiting for a retraining plan';
    case 'acknowledged':
      return 'In a retraining plan, awaiting Admin approval';
    case 'resolved':
      return 'Retrained';
    default:
      return 'Not sent yet';
  }
}
