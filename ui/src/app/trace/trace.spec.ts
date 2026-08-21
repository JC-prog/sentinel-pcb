import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { EMPTY } from 'rxjs';
import { SseService } from '../sse.service';
import { Trace } from './trace';

describe('Trace', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Trace],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'wf-test-2' }) } },
        },
        // Real EventSource isn't available/meaningful in the test DOM - stub the stream.
        { provide: SseService, useValue: { connect: () => EMPTY } },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create and show the current status once loaded', () => {
    const fixture = TestBed.createComponent(Trace);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/workflows/wf-test-2')).flush({
      workflow_id: 'wf-test-2',
      status: 'PREPARING',
      board_id: 'MB-2024-REV3',
      component_id: 'R47',
      recipe_id: 'RCP-1',
      image_filename: 'board.png',
      decision: null,
      overall_confidence: null,
      feature_confidence: null,
      defect_label: null,
      created_at: '2026-08-16T00:00:00Z',
      completed_at: null,
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('PREPARING');
  });
});
