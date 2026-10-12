import { WorkflowCorrection } from './models/orchestrator.models';
import { formatBytes, labelText, machineSaid, modelNote, percent, sourceLabel, trainingStatus } from './work-format';

const base = {
  agent1_label: null,
  region_guess: null,
  queueable: true,
  queued: false,
  ticket_status: null,
} as unknown as WorkflowCorrection;

describe('work-format', () => {
  it('formats byte counts', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('formats percentages and missing values', () => {
    expect(percent(0.5)).toBe('50%');
    expect(percent(null)).toBe('—');
  });

  it('normalises defect labels the way the review shows them', () => {
    expect(labelText('MissingPart_3')).toBe('missing part');
    expect(labelText('solder-insuffcient')).toBe('solder insufficient');
    expect(labelText('Golden')).toBe('no defect');
    expect(labelText('tombstone')).toBe('tombstone');
    expect(labelText(null)).toBe('—');
  });

  it('names decision sources', () => {
    expect(sourceLabel('MACHINE')).toBe('Accept Machine');
    expect(sourceLabel('AI')).toBe('Accept AI');
    expect(sourceLabel('OTHER')).toBe('OTHER');
    expect(sourceLabel(null)).toBe('—');
  });

  it('shows the same machine verdict as the Review Console: the defect prediction, else the AOI call', () => {
    expect(machineSaid({ ...base, agent1_label: 'WrongPart_13', machine_label: 'Shift' })).toBe('wrong part');
    expect(machineSaid({ ...base, machine_label: 'MissingPart' })).toBe('missing part');
    expect(machineSaid(base)).toBe('—');
  });

  it('explains a sample the defect model never saw, without presenting the region as a defect', () => {
    expect(modelNote({ ...base, outcome: 'relabelled' } as WorkflowCorrection)).toBe('');
    expect(modelNote({ ...base, outcome: 'no_prediction' } as WorkflowCorrection)).toBe('The defect model did not run.');
    expect(
      modelNote({ ...base, outcome: 'no_prediction', region_guess: { label: 'Body', confidence: 0.4 } } as WorkflowCorrection),
    ).toBe('The defect model did not run: the region model was unsure (Body area 40%).');
  });

  it('describes where a correction is on its way to retraining', () => {
    expect(trainingStatus({ ...base, queueable: false })).toContain("Can't send");
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'open' })).toContain('waiting for a retraining plan');
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'acknowledged' })).toContain('Admin approval');
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'resolved' })).toBe('Retrained');
    expect(trainingStatus(base)).toBe('Not sent yet');
  });
});
