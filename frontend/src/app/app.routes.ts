import { Routes } from '@angular/router';

import { HealthComponent } from './health/health.component';

export const routes: Routes = [
  { path: '', component: HealthComponent },
  { path: '**', redirectTo: '' },
];
