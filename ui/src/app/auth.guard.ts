import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';

export const authGuard: CanActivateFn = async () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  if (authService.currentUser() === undefined) {
    await authService.fetchCurrentUser();
  }
  return authService.currentUser() ? true : router.parseUrl('/login');
};
