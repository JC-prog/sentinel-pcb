import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CaseChat } from './case-chat';

describe('CaseChat', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CaseChat],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function askViaForm(fixture: ReturnType<typeof TestBed.createComponent<CaseChat>>, question: string) {
    const compiled = fixture.nativeElement as HTMLElement;
    const input = compiled.querySelector('input') as HTMLInputElement;
    input.value = question;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    (compiled.querySelector('button') as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('sends the typed question and renders the returned precedents and guidance', () => {
    const fixture = TestBed.createComponent(CaseChat);
    fixture.componentInstance.workflowId = 'wf-test-3';
    fixture.detectChanges();

    askViaForm(fixture, 'any precedents for a Void defect?');

    const req = httpMock.expectOne((r) => r.url.endsWith('/workflows/wf-test-3/case-context'));
    expect(req.request.body).toEqual({ question: 'any precedents for a Void defect?' });
    req.flush({
      precedents: [
        {
          workflow_id: 'wf-1',
          board_id: 'MB-2024-REV3',
          component_id: 'C12',
          defect_label: 'Void',
          decision: 'escalate_to_human',
        },
      ],
      guidance: [{ title: 'Void handling', defect_label: 'Void', content: 'Rework the joint.' }],
    });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('MB-2024-REV3');
    expect(compiled.textContent).toContain('Rework the joint.');
  });

  it('shows an error message when the request fails', () => {
    const fixture = TestBed.createComponent(CaseChat);
    fixture.componentInstance.workflowId = 'wf-test-3';
    fixture.detectChanges();

    askViaForm(fixture, 'what happened here?');

    httpMock
      .expectOne((r) => r.url.endsWith('/workflows/wf-test-3/case-context'))
      .flush('error', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain("Couldn't reach Case Context");
  });
});
