import { OrchestratorLogEntry } from './models/orchestrator.models';
import { formatLog, logEntryText } from './work-log';

describe('work-log', () => {
  const entries: OrchestratorLogEntry[] = [
    { kind: 'log', text: 'hello', at: 0 },
    { kind: 'error', message: 'oops', at: 0 },
  ];

  it('renders a plain line and an error line as text', () => {
    expect(logEntryText(entries[0])).toContain('hello');
    expect(logEntryText(entries[1])).toContain('oops');
  });

  it('joins every entry into the downloadable log, one per line', () => {
    const lines = formatLog(entries).trimEnd().split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[1]).toContain('ERROR');
  });
});
