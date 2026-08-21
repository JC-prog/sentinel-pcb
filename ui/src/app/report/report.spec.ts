import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { Report } from './report';

describe('Report', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Report],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'wf-test-3' }) } },
        },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create and render the recommendation once loaded', () => {
    const fixture = TestBed.createComponent(Report);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/workflows/wf-test-3')).flush({
      workflow_id: 'wf-test-3',
      status: 'HUMAN_REVIEW',
      board_id: 'MB-2024-REV3',
      component_id: 'C12',
      recipe_id: 'RCP-1',
      image_filename: 'board.png',
      decision: 'escalate_to_human',
      overall_confidence: 0.6,
      feature_confidence: 0.9,
      defect_label: 'Solder Bridge',
      created_at: '2026-08-16T00:00:00Z',
      completed_at: '2026-08-16T00:00:01Z',
      metadata: {},
      detections: [],
      rationale: 'low confidence',
      explanation: {
        claims: [
          { text: 'Feature detection found 1 feature.', supported: true, evidence_ref: 'x' },
        ],
        stripped_claims: [],
        grounded_flag: true,
        recommendation: 'Escalate for human review.',
        retrieved_precedents: [],
        retrieved_guidance: [],
        prompt_version: 'template-v1',
      },
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Escalate for human review.');
  });

  it('shows a fallback message when the workflow has no report', () => {
    const fixture = TestBed.createComponent(Report);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/workflows/wf-test-3')).flush({
      workflow_id: 'wf-test-3',
      status: 'LEARNING_QUEUE',
      board_id: 'MB-2024-REV3',
      component_id: 'R47',
      recipe_id: 'RCP-1',
      image_filename: 'board.png',
      decision: 'auto_accept',
      overall_confidence: 0.97,
      feature_confidence: 0.95,
      defect_label: null,
      created_at: '2026-08-16T00:00:00Z',
      completed_at: '2026-08-16T00:00:01Z',
      metadata: {},
      detections: [],
      rationale: 'ok',
      explanation: null,
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain("wasn't escalated for review");
  });
});
