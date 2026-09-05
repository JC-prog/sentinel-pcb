import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../auth.service';
import { Register } from './register';

describe('Register', () => {
  let fixture: ComponentFixture<Register>;
  let authService: AuthService;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Register],
      providers: [provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(Register);
    authService = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('defaults the role to qa', () => {
    expect(fixture.componentInstance['role']()).toBe('qa');
  });

  it('registers with the entered fields and navigates to the chat home on success', async () => {
    vi.spyOn(authService, 'register').mockResolvedValue(undefined);
    vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);

    const component = fixture.componentInstance;
    component['name'].set('Jane QA');
    component['email'].set('jane@example.com');
    component['password'].set('correct-horse-battery-staple');
    component['employeeId'].set('EMP-042');
    component['departmentShift'].set('QA Day Shift');
    component['role'].set('operator');
    await component.submit();

    expect(authService.register).toHaveBeenCalledWith({
      name: 'Jane QA',
      email: 'jane@example.com',
      password: 'correct-horse-battery-staple',
      employeeId: 'EMP-042',
      departmentShift: 'QA Day Shift',
      role: 'operator',
    });
    expect(router.navigateByUrl).toHaveBeenCalledWith('/');
  });

  it('shows an error message on failure', async () => {
    vi.spyOn(authService, 'register').mockRejectedValue(new Error('email already registered'));

    await fixture.componentInstance.submit();
    fixture.detectChanges();

    expect(fixture.componentInstance['error']()).toBe('email already registered');
  });
});
