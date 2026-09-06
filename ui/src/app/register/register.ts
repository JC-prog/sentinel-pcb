import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthService, UserRole } from '../auth.service';

@Component({
  imports: [FormsModule, RouterLink],
  selector: 'app-register',
  styleUrl: './register.css',
  templateUrl: './register.html',
})
export class Register {
  protected readonly username = signal('');
  protected readonly email = signal('');
  protected readonly password = signal('');
  protected readonly employeeId = signal('');
  protected readonly departmentShift = signal('');
  protected readonly role = signal<UserRole>('qa');
  protected readonly error = signal<string | null>(null);
  protected readonly submitting = signal(false);

  constructor(
    private readonly authService: AuthService,
    private readonly router: Router,
  ) {}

  async submit(): Promise<void> {
    if (this.submitting()) {
      return;
    }
    this.error.set(null);
    this.submitting.set(true);
    try {
      await this.authService.register({
        username: this.username(),
        email: this.email(),
        password: this.password(),
        employeeId: this.employeeId(),
        departmentShift: this.departmentShift(),
        role: this.role(),
      });
      await this.router.navigateByUrl('/');
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      this.submitting.set(false);
    }
  }
}
