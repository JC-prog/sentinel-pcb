import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Observability } from './observability';

describe('Observability', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Observability],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create and render metrics once loaded', () => {
    const fixture = TestBed.createComponent(Observability);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/observability/metrics')).flush({
      generated_at: '2026-08-16T00:00:00Z',
      total_workflows: 5,
      decision_counts: { auto_accept: 3, escalate_to_human: 2 },
      escalation_rate: 0.4,
      avg_feature_confidence: 0.9,
      avg_defect_confidence: 0.7,
      disagreement_rate: 0.1,
      policy_violation_count: 0,
      alerts: [],
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('No active alerts.');
  });

  it('renders an alert message when one is present', () => {
    const fixture = TestBed.createComponent(Observability);
    fixture.detectChanges();

    httpMock.expectOne((r) => r.url.endsWith('/observability/metrics')).flush({
      generated_at: '2026-08-16T00:00:00Z',
      total_workflows: 5,
      decision_counts: { escalate_to_human: 5 },
      escalation_rate: 1.0,
      avg_feature_confidence: 0.9,
      avg_defect_confidence: 0.5,
      disagreement_rate: 0.4,
      policy_violation_count: 0,
      alerts: [
        {
          metric: 'escalation_rate',
          value: 1.0,
          threshold: 0.5,
          severity: 'warning',
          message: 'Escalation rate 100% exceeds 50%.',
        },
      ],
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Escalation rate 100% exceeds 50%.');
  });
});
