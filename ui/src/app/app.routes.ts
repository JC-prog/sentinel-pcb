import { Routes } from '@angular/router';
import { BoardDetail } from './board/board-detail';
import { History } from './history/history';
import { Observability } from './observability/observability';
import { Queue } from './queue/queue';
import { Report } from './report/report';
import { Trace } from './trace/trace';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'queue' },
  { path: 'queue', component: Queue },
  { path: 'board/:id', component: BoardDetail },
  { path: 'trace/:id', component: Trace },
  { path: 'report/:id', component: Report },
  { path: 'history', component: History },
  { path: 'observability', component: Observability },
];
