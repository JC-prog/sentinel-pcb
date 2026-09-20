import { Routes } from '@angular/router';
import { authGuard } from './auth.guard';
import { Chat } from './chat/chat';
import { Login } from './login/login';
import { Register } from './register/register';
import { Work } from './work/work';

export const routes: Routes = [
  { path: 'login', component: Login },
  { path: 'register', component: Register },
  // Work is the default landing tab - '/' redirects there rather than rendering Chat directly,
  // so login/register's navigateByUrl('/') lands on Work without needing to know that's the
  // default, and Chat gets its own explicit path ('/chat') for its no-conversation-selected
  // landing state instead of owning '/'.
  { path: '', pathMatch: 'full', redirectTo: 'work' },
  { path: 'chat', component: Chat, canActivate: [authGuard] },
  { path: 'c/:id', component: Chat, canActivate: [authGuard] },
  { path: 'work', component: Work, canActivate: [authGuard] },
];
