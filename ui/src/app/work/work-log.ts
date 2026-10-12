import { OrchestratorLogEntry } from './models/orchestrator.models';

/** `HH:MM:SS` in local time; empty for an entry that carries no timestamp. */
export function logTime(at: number | undefined): string {
  if (at === undefined) {
    return '';
  }
  const date = new Date(at);
  const two = (value: number): string => String(value).padStart(2, '0');
  return `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
}

/** What one log entry says, as plain text - the same words the on-screen row and the downloaded
 * file use. */
export function logEntryText(entry: OrchestratorLogEntry): string {
  switch (entry.kind) {
    case 'log':
      return entry.text;
    case 'error':
      return `ERROR: ${entry.message}`;
    case 'plan_step': {
      const step = entry.step;
      const rejected =
        step.policy && !step.policy.allowed ? ` (policy rejected: ${step.policy.reason})` : '';
      return `Step ${step.plan_version} [${step.planner_source}] ${step.decision} - ${step.reason}${rejected}`;
    }
  }
}

/** The whole log as a text file's contents: one line per entry, `[HH:MM:SS]` first. */
export function formatLog(entries: readonly OrchestratorLogEntry[]): string {
  if (entries.length === 0) {
    return '';
  }
  const lines = entries.map((entry) => {
    const time = logTime(entry.at);
    return time ? `[${time}] ${logEntryText(entry)}` : logEntryText(entry);
  });
  return `${lines.join('\n')}\n`;
}
