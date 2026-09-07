import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { App } from './app';
import { BackendStatusService } from './backend-status.service';
import { CHAT_RESPONDER } from './chat-responder';
import { of } from 'rxjs';

const backendStatusStub = {
  status: signal<'unknown' | 'online' | 'offline'>('online'),
  checking: signal(false),
  checkNow: () => Promise.resolve(),
};

@Component({ template: '', selector: 'app-test-stub' })
class StubComponent {}

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([
          { path: '', component: StubComponent },
          { path: 'login', component: StubComponent },
          { path: 'register', component: StubComponent },
        ]),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of('mock reply') } },
        { provide: BackendStatusService, useValue: backendStatusStub },
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the sidebar', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-sidebar')).toBeTruthy();
  });

  it('hides the sidebar on the login route', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/login');

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-sidebar')).toBeFalsy();
  });

  it('hides the sidebar on the register route', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/register');

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-sidebar')).toBeFalsy();
  });

  it('shows the sidebar again after navigating away from login', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/login');

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();

    await router.navigateByUrl('/');
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-sidebar')).toBeTruthy();
  });
});
