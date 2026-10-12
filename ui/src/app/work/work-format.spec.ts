import { WorkflowCorrection } from './models/orchestrator.models';
import { formatBytes, labelText, modelSaid, percent, sourceLabel, trainingStatus } from './work-format';

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

  it('describes what the model said, including a region guess when it could not decide', () => {
    expect(modelSaid({ ...base, agent1_label: 'WrongPart_13' })).toBe('wrong part');
    expect(modelSaid(base)).toBe('unsure');
    expect(modelSaid({ ...base, region_guess: { label: 'Body', confidence: 0.95 } })).toBe(
      'unsure (first guess: Body area 95%)',
    );
  });

  it('describes where a correction is on its way to retraining', () => {
    expect(trainingStatus({ ...base, queueable: false })).toContain("Can't send");
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'open' })).toContain('waiting for a retraining plan');
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'acknowledged' })).toContain('Admin approval');
    expect(trainingStatus({ ...base, queued: true, ticket_status: 'resolved' })).toBe('Retrained');
    expect(trainingStatus(base)).toBe('Not sent yet');
  });
});
