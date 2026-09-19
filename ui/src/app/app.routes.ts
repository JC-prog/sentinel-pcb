import { Routes } from '@angular/router';
import { authGuard } from './auth.guard';
import { Chat } from './chat/chat';
import { Login } from './login/login';
import { Register } from './register/register';
import { Work } from './work/work';

export const routes: Routes = [
  { path: 'login', component: Login },
  { path: 'register', component: Register },
  { path: '', component: Chat, canActivate: [authGuard] },
  { path: 'c/:id', component: Chat, canActivate: [authGuard] },
  { path: 'work', component: Work, canActivate: [authGuard] },
];
