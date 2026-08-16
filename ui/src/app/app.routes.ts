import { Routes } from '@angular/router';
import { BoardDetail } from './board/board-detail';
import { History } from './history/history';
import { Queue } from './queue/queue';
import { Trace } from './trace/trace';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'queue' },
  { path: 'queue', component: Queue },
  { path: 'board/:id', component: BoardDetail },
  { path: 'trace/:id', component: Trace },
  { path: 'history', component: History },
];
