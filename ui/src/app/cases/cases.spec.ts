import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';
import { CasesClient } from './cases-client';
import { Cases } from './cases';
import { CaseDetail, CaseList, CaseSummary } from './cases.models';

function summary(over: Partial<CaseSummary> = {}): CaseSummary {
  return {
    case_number: 'CASE-000001',
    status: 'review_required',
    board_id: 'BOARD-1',
    component_ref: 'U7',
    region: 'Body',
    defect_model: 'pcb_body_defect',
    defect_model_version: 'JcProg/body@v1',
    defect_label: 'MissingPart',
    defect_confidence: 0.82,
    corrected_label: null,
    created_by: 'jane-qa',
    created_at: '2026-10-12T14:05:00+00:00',
    ...over,
  };
}

function detail(over: Partial<CaseDetail> = {}): CaseDetail {
  return {
    ...summary(),
    package: 'QFN32',
    feature: null,
    issue_symptom: null,
    region_confidence: 0.9,
    region_model_version: 'JcProg/region@v1',
    defect_scores: { Golden: 0.1, MissingPart: 0.82 },
    measurement_validation: null,
    observations: ['Workflow finalized as review_required.'],
    image_id: 'board.png',
    inspection_xml_id: null,
    correction_reason: null,
    corrected_by: null,
    corrected_at: null,
    resolved_by: null,
    resolved_at: null,
    resolution_note: null,
    pending_label: null,
    pending_reason: null,
    ticket: null,
    conversation_id: null,
    ...over,
  };
}

describe('Cases', () => {
  let fixture: ComponentFixture<Cases>;
  let params: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  let client: {
    list: ReturnType<typeof vi.fn>;
    detail: ReturnType<typeof vi.fn>;
    imageUrl: ReturnType<typeof vi.fn>;
  };

  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? '';

  async function setup(list: CaseList, caseParam: string | null = null): Promise<void> {
    params = new BehaviorSubject(convertToParamMap(caseParam ? { case: caseParam } : {}));
    client = {
      list: vi.fn().mockResolvedValue(list),
      detail: vi.fn().mockResolvedValue(detail()),
      imageUrl: vi.fn().mockResolvedValue(null),
    };
    await TestBed.configureTestingModule({
      imports: [Cases],
      providers: [
        provideRouter([]),
        { provide: CasesClient, useValue: client },
        { provide: ActivatedRoute, useValue: { queryParamMap: params.asObservable() } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Cases);
    fixture.detectChanges();
    await settle();
  }

  async function settle(): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
  }

  it('lists the cases with the model label and any correction', async () => {
    await setup({
      total: 2,
      limit: 25,
      offset: 0,
      cases: [
        summary({ case_number: 'CASE-000002', corrected_label: 'Golden' }),
        summary({ case_number: 'CASE-000001' }),
      ],
    });

    expect(text()).toContain('CASE-000002');
    expect(text()).toContain('CASE-000001');
    expect(text()).toContain('Golden');
    expect(text()).toContain('1-2 of 2');
  });

  it('says there are no cases yet, and how one gets made', async () => {
    await setup({ total: 0, limit: 25, offset: 0, cases: [] });

    expect(text()).toContain('No cases yet');
    expect(text()).toContain('say its label is wrong');
  });

  it('says when filters match nothing', async () => {
    await setup({ total: 0, limit: 25, offset: 0, cases: [] });
    const component = fixture.componentInstance as unknown as { q: { set(v: string): void }; filtersChanged(): void };

    component.q.set('zzz');
    component.filtersChanged();
    await settle();

    expect(text()).toContain('No cases match these filters');
    expect(client.list).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'zzz', offset: 0 }));
  });

  it('opens the case named in the URL, showing the correction and its ticket', async () => {
    await setup({ total: 1, limit: 25, offset: 0, cases: [summary()] });
    client.detail.mockResolvedValue(
      detail({
        corrected_label: 'Golden',
        correction_reason: 'the pads are fine',
        corrected_by: 'jane-qa',
        ticket: {
          id: 'ticket-12345678',
          status: 'open',
          job_id: null,
          model_name: 'pcb_body_defect',
          observed_label: 'MissingPart',
          correct_label: 'Golden',
        },
      }),
    );
    params.next(convertToParamMap({ case: 'CASE-000001' }));
    await settle();
    await settle();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="case-title"]')?.textContent).toContain('CASE-000001');
    expect(el.querySelector('[data-testid="correction"]')?.textContent).toContain('the pads are fine');
    expect(el.querySelector('[data-testid="ticket"]')?.textContent).toContain('ticket-1');
    expect(client.detail).toHaveBeenCalledWith('CASE-000001');
  });

  it('shows a relabel that was proposed but not confirmed as pending, not as a correction', async () => {
    await setup({ total: 1, limit: 25, offset: 0, cases: [summary()] });
    client.detail.mockResolvedValue(detail({ pending_label: 'Tombstone', pending_reason: 'lifted' }));
    params.next(convertToParamMap({ case: 'CASE-000001' }));
    await settle();
    await settle();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="pending"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="correction"]')).toBeNull();
  });

  it('only offers the conversation to its owner', async () => {
    await setup({ total: 1, limit: 25, offset: 0, cases: [summary()] }, 'CASE-000001');
    expect(text()).not.toContain('Open the conversation');

    client.detail.mockResolvedValue(detail({ conversation_id: 'conv-1' }));
    params.next(convertToParamMap({ case: 'CASE-000002' }));
    await settle();
    await settle();

    expect(text()).toContain('Open the conversation');
  });

  it('shows an error for a case that cannot be loaded', async () => {
    await setup({ total: 0, limit: 25, offset: 0, cases: [] });
    client.detail.mockRejectedValue(new Error('case not found'));
    params.next(convertToParamMap({ case: 'CASE-999999' }));
    await settle();
    await settle();

    expect(text()).toContain('case not found');
  });

  it('puts the chosen case in the URL when a row is clicked', async () => {
    await setup({ total: 1, limit: 25, offset: 0, cases: [summary()] });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    (fixture.nativeElement.querySelector('section[aria-label="Cases"] button') as HTMLElement).click();

    expect(navigate).toHaveBeenCalledWith([], { queryParams: { case: 'CASE-000001' } });
  });
});
