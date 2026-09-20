import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { routes } from './app.routes';
import { AuthService, AuthUser } from './auth.service';
import { CHAT_RESPONDER } from './chat/chat-responder';

const USER: AuthUser = {
  id: 'user-1',
  username: 'jane-qa',
  email: 'jane@example.com',
  employeeId: 'EMP-042',
  departmentShift: 'QA Day Shift',
  role: 'qa',
};

/** Exercises the real app.routes.ts (unlike app.spec.ts's own isolated stub route table) to
 * confirm Work is genuinely the default landing tab - '/' redirects to '/work', not just "nothing
 * currently makes Chat load first" by omission. */
describe('routes', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        { provide: CHAT_RESPONDER, useValue: { respond: () => of({ type: 'delta', text: 'mock reply' }) } },
      ],
    }).compileComponents();

    const authService = TestBed.inject(AuthService);
    authService.currentUser.set(USER); // already logged in - authGuard should let navigation through
  });

  it('redirects "/" to "/work"', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    expect(router.url).toBe('/work');
  });

  it('serves the Models tab at "/models"', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/models');
    expect(router.url).toBe('/models');
  });

  it('still serves Chat at the explicit "/chat" path', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/chat');
    expect(router.url).toBe('/chat');
  });
});
