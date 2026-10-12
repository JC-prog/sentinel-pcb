import { WorkflowCorrection } from './models/orchestrator.models';
import { batchHint, correctionKey, groupCorrections, labelMix, modelLabel } from './work-feedback';

function correction(overrides: Partial<WorkflowCorrection> = {}): WorkflowCorrection {
  return {
    sample_id: 'S1',
    kind: 'defect',
    model_name: 'pcb_body_defect',
    model_version: null,
    agent1_label: 'MissingPart',
    machine_label: 'WrongPart_13',
    agent2_label: null,
    final_result: 'tombstone',
    selected_source: 'MANUAL',
    operator_notes: null,
    outcome: 'relabelled',
    queueable: true,
    queued: false,
    ticket_status: null,
    ...overrides,
  } as WorkflowCorrection;
}

describe('work-feedback', () => {
  it('keys a correction by sample and model, so one sample can carry two', () => {
    expect(correctionKey(correction())).toBe('S1|pcb_body_defect');
    expect(correctionKey(correction({ model_name: 'pcb_region' }))).toBe('S1|pcb_region');
  });

  it('names models in plain words', () => {
    expect(modelLabel('pcb_region')).toBe('Region model');
    expect(modelLabel('pcb_text_defect')).toBe('Text defect model');
    expect(modelLabel(null)).toBe('No model to route to');
    expect(modelLabel('pcb_other')).toBe('pcb_other');
  });

  it('counts the label mix, most common first', () => {
    const mix = labelMix([
      correction({ final_result: 'tombstone' }),
      correction({ sample_id: 'S2', final_result: 'shifted' }),
      correction({ sample_id: 'S3', final_result: 'tombstone' }),
    ]);
    expect(mix.map((m) => [m.label, m.count])).toEqual([
      ['tombstone', 2],
      ['shifted', 1],
    ]);
    expect(Math.round(mix[0].share)).toBe(67);
  });

  it('hints at tiny and lopsided batches only', () => {
    expect(batchHint(0, [])).toBeNull();
    expect(batchHint(3, [{ label: 'a', count: 3, share: 100 }])).toContain('Small batch');
    expect(batchHint(10, [{ label: 'tombstone', count: 9, share: 90 }])).toContain('Mostly one label');
    expect(
      batchHint(10, [
        { label: 'a', count: 5, share: 50 },
        { label: 'b', count: 5, share: 50 },
      ]),
    ).toBeNull();
  });

  it('groups by the model taught: region first, defect models after, unroutable last', () => {
    const groups = groupCorrections([
      correction({ sample_id: 'S1', model_name: 'pcb_text_defect' }),
      correction({ sample_id: 'S2', model_name: null, queueable: false }),
      correction({ sample_id: 'S3', model_name: 'pcb_region', kind: 'region', final_result: 'Lead' }),
      correction({ sample_id: 'S4', model_name: 'pcb_body_defect', queued: true, ticket_status: 'acknowledged' }),
    ]);

    expect(groups.map((g) => g.modelName)).toEqual(['pcb_region', 'pcb_body_defect', 'pcb_text_defect', null]);
    const body = groups[1];
    expect(body.pending).toHaveLength(0);
    expect(body.inPlan).toBe(1);
    expect(groups[3].pending).toHaveLength(0); // nothing sendable without a model
  });
});
