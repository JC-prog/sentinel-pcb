import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../auth.service';
import { Login } from './login';

describe('Login', () => {
  let fixture: ComponentFixture<Login>;
  let authService: AuthService;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Login],
      providers: [provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(Login);
    authService = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('logs in and navigates to the chat home on success', async () => {
    vi.spyOn(authService, 'login').mockResolvedValue(undefined);
    vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    fixture.componentInstance['username'].set('test-qa');
    fixture.componentInstance['password'].set('correct-horse-battery-staple');
    await fixture.componentInstance.submit();

    expect(authService.login).toHaveBeenCalledWith('test-qa', 'correct-horse-battery-staple');
    expect(router.navigateByUrl).toHaveBeenCalledWith('/');
  });

  it('shows an error message and does not navigate on failure', async () => {
    vi.spyOn(authService, 'login').mockRejectedValue(new Error('incorrect username or password'));
    const navigateSpy = vi.spyOn(router, 'navigateByUrl');

    await fixture.componentInstance.submit();
    fixture.detectChanges();

    expect(fixture.componentInstance['error']()).toBe('incorrect username or password');
    expect(navigateSpy).not.toHaveBeenCalled();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'incorrect username or password',
    );
  });
});
