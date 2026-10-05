import { ComponentFixture, TestBed } from '@angular/core/testing';
import { InspectionResult, ToolResult } from '../models/chat.models';
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

      expect(text(el, 'case-state')).toBe('Not saved as a case');
      expect(text(el, 'verdict')).toBe('Accepted');
      expect(text(el, 'region')).toBe('Body · 91%');
      expect(text(el, 'defect')).toBe('MissingPart · 87%');
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
});
