import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Queue } from './queue';

describe('Queue', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Queue],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create and poll the active workflows list', () => {
    const fixture = TestBed.createComponent(Queue);
    fixture.detectChanges();
    expect(fixture.componentInstance).toBeTruthy();

    const req = httpMock.expectOne((r) => r.url.endsWith('/workflows'));
    req.flush([]);
  });

  it('disables submit until a file is selected', () => {
    const fixture = TestBed.createComponent(Queue);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith('/workflows')).flush([]);

    const button = (fixture.nativeElement as HTMLElement).querySelector('button');
    expect(button?.disabled).toBe(true);
  });
});
