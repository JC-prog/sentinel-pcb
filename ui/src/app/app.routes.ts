import { Routes } from '@angular/router';
import { Chat } from './chat/chat';

export const routes: Routes = [
  { path: '', component: Chat },
  { path: 'c/:id', component: Chat },
];
