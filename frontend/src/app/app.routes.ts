import { Routes } from '@angular/router';

import { BriefPageComponent } from './features/brief/brief-page.component';
import { PlaceholderPageComponent } from './features/placeholder/placeholder-page.component';

const soon = (path: string, heading: string, icon: string, description: string) => ({
  path,
  component: PlaceholderPageComponent,
  title: `${heading} · FrogRadar`,
  data: { heading, icon, description },
});

// The Brief loads eagerly (first screen); the other pages are lazy chunks.
export const routes: Routes = [
  { path: '', component: BriefPageComponent, title: 'Morning Brief · FrogRadar' },
  { path: 'signals', loadComponent: () => import('./features/signals/signals-page.component').then((m) => m.SignalsPageComponent), title: 'Signals · FrogRadar' },
  { path: 'battlecards', loadComponent: () => import('./features/battlecards/battlecards-redirect.component').then((m) => m.BattlecardsRedirectComponent), title: 'Battlecards · FrogRadar' },
  { path: 'battlecards/:slug', loadComponent: () => import('./features/battlecards/battlecard-page.component').then((m) => m.BattlecardPageComponent), title: 'Battlecard · FrogRadar' },
  { path: 'radar', loadComponent: () => import('./features/radar/radar-page.component').then((m) => m.RadarPageComponent), title: 'Competitor Radar · FrogRadar' },
  { path: 'radar/:id', loadComponent: () => import('./features/radar/radar-detail-page.component').then((m) => m.RadarDetailPageComponent), title: 'Radar candidate · FrogRadar' },
  soon('ask', 'Ask', 'ask', 'Ask questions about the market and get answers that cite their sources.'),
  soon('missions', 'Missions', 'missions', 'Give a research agent a task and follow its steps until the report is ready.'),
  soon('review', 'Review queue', 'review', 'Approve, reject or correct new insights; corrections teach the pipeline.'),
  { path: '**', redirectTo: '' },
];
