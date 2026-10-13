import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Explanation, InspectionResult, ToolResult } from '../models/chat.models';
import { ToolResultCard } from './tool-result-card';

const ACCEPTED: InspectionResult = {
  case_created: false,
  verdict: 'accepted',
  review_required: false,
  review_reasons: [],
  inspection_summary: null,
  region: {
    model: 'pcb_region',
    model_version: 'JcProg/region@v1',
    label: 'Body',
    confidence: 0.912,
    top_scores: [{ label: 'Body', score: 0.912 }],
  },
  defect: {
    model: 'pcb_body_defect',
    model_version: 'JcProg/body@v1',
    label: 'MissingPart',
    confidence: 0.8731,
    top_scores: [
      { label: 'MissingPart', score: 0.8731 },
      { label: 'Golden', score: 0.1 },
    ],
  },
  measurement_validation: null,
};

function render(toolResult: ToolResult): { fixture: ComponentFixture<ToolResultCard>; el: HTMLElement } {
  const fixture = TestBed.createComponent(ToolResultCard);
  fixture.componentRef.setInput('toolResult', toolResult);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

const text = (el: HTMLElement, testId: string): string =>
  el.querySelector(`[data-testid="${testId}"]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

describe('ToolResultCard', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [ToolResultCard] }));

  describe('an inspection result', () => {
    it('shows the verdict and what each classifier found, and that no case was saved', () => {
      const { el } = render({ name: 'inspect_image', result: ACCEPTED });

      expect(text(el, 'case-state')).toBe('Not saved as a case yet');
      expect(text(el, 'verdict')).toBe('Accepted');
      expect(text(el, 'region')).toContain('Body · 91%');
      expect(text(el, 'defect')).toContain('MissingPart · 87%');
    });

    it('says which image it is when several were attached', () => {
      const { el } = render({
        name: 'inspect_image',
        result: { ...ACCEPTED, image_number: 2, images_attached: 3 },
      });

      expect(text(el, 'card-title')).toContain('image 2 of 3');
      expect(text(render({ name: 'inspect_image', result: ACCEPTED }).el, 'card-title')).not.toContain('image');
    });

    it('shows which model version answered, and a confidence meter for each stage', () => {
      const { el } = render({ name: 'inspect_image', result: ACCEPTED });

      expect(text(el, 'region')).toContain('JcProg/region@v1');
      expect(text(el, 'defect')).toContain('JcProg/body@v1');
      const meters = Array.from(el.querySelectorAll('[role="meter"]')).map((m) =>
        m.getAttribute('aria-valuenow'),
      );
      expect(meters).toEqual(['91', '87']);
    });

    it("shows the assistant's one-line summary of the inspection when there is one", () => {
      const { el } = render({
        name: 'inspect_image',
        result: { ...ACCEPTED, inspection_summary: 'Clean pads, no visible defect.' },
      });

      expect(text(el, 'summary')).toBe('Clean pads, no visible defect.');
      expect(render({ name: 'inspect_image', result: ACCEPTED }).el.querySelector('[data-testid="summary"]')).toBeNull();
    });

    it('says what to do next: correct the label, or inspect a clearer image', () => {
      const classified = render({ name: 'inspect_image', result: ACCEPTED }).el;
      const unclassified = render({
        name: 'inspect_image',
        result: { ...ACCEPTED, defect: null, review_required: true, verdict: 'review_required' },
      }).el;

      expect(text(classified, 'next-step')).toContain('tell me the right one');
      expect(text(unclassified, 'next-step')).toContain('no label to correct');
    });

    it('lists what else the model considered, best first', () => {
      const { el } = render({ name: 'inspect_image', result: ACCEPTED });

      const scores = text(el, 'scores');
      expect(scores).toContain('MissingPart');
      expect(scores.indexOf('MissingPart')).toBeLessThan(scores.indexOf('Golden'));
    });

    it('flags a case that needs review and says why', () => {
      const { el } = render({
        name: 'inspect_image',
        result: {
          ...ACCEPTED,
          verdict: 'review_required',
          review_required: true,
          review_reasons: ['defect confidence 0.55 is below the 0.70 threshold'],
        },
      });

      expect(text(el, 'verdict')).toBe('Review required');
      expect(el.textContent).toContain('defect confidence 0.55 is below the 0.70 threshold');
    });

    it('says the defect was not classified when the region was too uncertain', () => {
      const { el } = render({
        name: 'inspect_image',
        result: { ...ACCEPTED, defect: null, review_required: true, verdict: 'review_required' },
      });

      expect(text(el, 'defect')).toBe('not classified');
      expect(el.querySelector('[data-testid="scores"]')).toBeNull();
    });

    it('shows the measurement validation only when an inspection XML was checked', () => {
      const without = render({ name: 'inspect_image', result: ACCEPTED });
      expect(without.el.querySelector('[data-testid="measurements"]')).toBeNull();

      const failed = render({
        name: 'inspect_image',
        result: { ...ACCEPTED, measurement_validation: { valid: false, issues: ['X'] } },
      });
      expect(text(failed.el, 'measurements')).toBe('Failed validation');
    });
  });

  describe('a relabel', () => {
    it('shows a proposal as waiting for confirmation, with nothing saved yet', () => {
      const { el } = render({
        name: 'relabel_case',
        result: {
          status: 'awaiting_confirmation',
          case_number: 'CASE-000042',
          model: 'pcb_body_defect',
          model_label: 'MissingPart',
          proposed_label: 'Golden',
          reason: 'it is a golden part',
        },
      });

      const card = text(el, 'relabel-proposal');
      expect(card).toContain('MissingPart');
      expect(card).toContain('Golden');
      expect(card).toContain('it is a golden part');
      expect(card).toContain('Nothing is saved yet');
      expect(el.querySelector('[data-testid="relabel-done"]')).toBeNull();
    });

    it('shows a confirmed relabel with its retraining ticket', () => {
      const { el } = render({
        name: 'confirm_relabel',
        result: {
          status: 'relabelled',
          case_number: 'CASE-000042',
          model: 'pcb_body_defect',
          model_label: 'MissingPart',
          corrected_label: 'Golden',
          ticket_id: 'ticket-9',
        },
      });

      const card = text(el, 'relabel-done');
      expect(card).toContain('Golden');
      expect(card).toContain('Queued for retraining');
      expect(card).toContain('ticket-9');
      expect(el.querySelector('[data-testid="relabel-proposal"]')).toBeNull();
    });
  });

  describe('a relabel on an inspection that is not a case yet', () => {
    it('says confirming also saves it as a new case', () => {
      const { el } = render({
        name: 'relabel_case',
        result: {
          status: 'awaiting_confirmation',
          case_number: null,
          model: 'pcb_body_defect',
          model_label: 'MissingPart',
          proposed_label: 'Golden',
          reason: 'the pads are fine',
        },
      });

      const card = text(el, 'relabel-proposal');
      expect(card).toContain('this inspection');
      expect(card).toContain('new case');
    });
  });

  describe('a case review', () => {
    it('shows a proposal as waiting for confirmation, with its meaning and nothing saved yet', () => {
      const { el } = render({
        name: 'review_case',
        result: {
          status: 'awaiting_confirmation',
          case_number: 'CASE-000042',
          model_label: 'MissingPart',
          decision: 'override',
          note: 'it was only a shadow',
        },
      });

      const card = text(el, 'review-proposal');
      expect(card).toContain('Override');
      expect(card).toContain('false positive');
      expect(card).toContain('it was only a shadow');
      expect(card).toContain('Nothing is saved yet');
      expect(el.querySelector('[data-testid="review-done"]')).toBeNull();
    });

    it('words an approval as the defect being confirmed', () => {
      const { el } = render({
        name: 'review_case',
        result: {
          status: 'awaiting_confirmation',
          case_number: 'CASE-000042',
          model_label: 'MissingPart',
          decision: 'approve',
          note: null,
        },
      });

      expect(text(el, 'review-proposal')).toContain('defect confirmed');
    });

    it('shows the resolved case with its new status', () => {
      const { el } = render({
        name: 'confirm_review',
        result: {
          status: 'reviewed',
          case_number: 'CASE-000042',
          model_label: 'MissingPart',
          decision: 'override',
          case_status: 'overridden',
          note: null,
        },
      });

      const card = text(el, 'review-done');
      expect(card).toContain('CASE-000042');
      expect(card).toContain('overridden');
      expect(el.querySelector('[data-testid="review-proposal"]')).toBeNull();
    });
  });

  describe('case created', () => {
    it('shows the case number, its status and what the model found', () => {
      const { el } = render({
        name: 'create_case',
        result: {
          status: 'created',
          case_number: 'CASE-000007',
          verdict: 'review_required',
          region: 'Body',
          defect: 'Bridge',
          defect_confidence: 0.6149,
        },
      });

      expect(text(el, 'case-number')).toBe('CASE-000007');
      expect(text(el, 'case-status')).toBe('Review required');
      const card = text(el, 'case-created');
      expect(card).toContain('Body');
      expect(card).toContain('Bridge');
      expect(card).toContain('61%');
      expect(card).toContain('tell me if the label is wrong');
      expect(card).not.toContain('approve');
    });

    it('says there is nothing to correct when no defect model ran', () => {
      const { el } = render({
        name: 'create_case',
        result: {
          status: 'created',
          case_number: 'CASE-000008',
          verdict: 'accepted',
          region: null,
          defect: null,
          defect_confidence: null,
        },
      });

      const card = text(el, 'case-created');
      expect(card).toContain('Accepted');
      expect(card).toContain('defect not classified');
      expect(card).toContain('no label to correct');
    });
  });

  describe('an explanation', () => {
    const EXPLANATION: Explanation = {
      status: 'ok',
      advisory: true,
      label: 'tombstone',
      confidence: 0.8,
      summary: 'One end of the part is lifted off its pad.',
      visual_evidence_cited: 'lifted at one end',
      ipc_precedent_cited: 'IPC-A-610 tombstone criteria',
      model_label: 'tombstone',
      agrees_with_model: true,
      guardrail: { status: 'PASSED', hallucination_flagged: false, note: null },
      evidence: { visual: 'lifted', telemetry_recorded: false, precedents_found: false },
    };

    it('shows what Agent 2 found, that it agrees, and that it is advisory', () => {
      const { el } = render({ name: 'explain_inspection', result: EXPLANATION });

      expect(text(el, 'explanation-label')).toContain('tombstone');
      expect(text(el, 'explanation-label')).toContain('80%');
      expect(text(el, 'agreement')).toBe('Agrees with the model');
      expect(text(el, 'explanation-summary')).toContain('lifted off its pad');
      expect(text(el, 'explanation')).toContain('Advisory only');
      expect(text(el, 'telemetry')).toBe('Not recorded');
    });

    it('says plainly when it disagrees and what the model said', () => {
      const { el } = render({
        name: 'explain_inspection',
        result: { ...EXPLANATION, label: 'missing part', model_label: 'tombstone', agrees_with_model: false },
      });

      expect(text(el, 'agreement')).toBe('Disagrees with the model');
      expect(text(el, 'explanation')).toContain('The model said tombstone');
    });

    it('does not pretend to decide when Agent 2 declined to', () => {
      const { el } = render({
        name: 'explain_inspection',
        result: { ...EXPLANATION, label: null, confidence: 0.2, agrees_with_model: null },
      });

      expect(text(el, 'explanation-label')).toContain('Could not decide');
      expect(text(el, 'agreement')).toBe('No comparison');
    });

    it('warns when its own checks flagged the explanation', () => {
      const { el } = render({
        name: 'explain_inspection',
        result: {
          ...EXPLANATION,
          status: 'flagged',
          guardrail: { status: 'FLAGGED', hallucination_flagged: true, note: 'Visual evidence is too vague.' },
        },
      });

      expect(text(el, 'flagged')).toContain('weakly grounded');
      expect(text(el, 'flagged')).toContain('too vague');
    });

    it('says when measurements were read from the inspection XML', () => {
      const { el } = render({
        name: 'explain_inspection',
        result: { ...EXPLANATION, evidence: { ...EXPLANATION.evidence, telemetry_recorded: true } },
      });

      expect(text(el, 'telemetry')).toBe('Read from the inspection XML');
    });
  });
});
