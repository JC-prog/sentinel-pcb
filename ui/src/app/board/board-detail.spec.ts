import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { BoardDetail } from './board-detail';

describe('BoardDetail', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'wf-test-1' }) } },
        },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create and render board info once loaded', () => {
    const fixture = TestBed.createComponent(BoardDetail);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/workflows/wf-test-1')).flush({
      workflow_id: 'wf-test-1',
      status: 'COMPLETED',
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
    expect(compiled.textContent).toContain('MB-2024-REV3');
  });
});
