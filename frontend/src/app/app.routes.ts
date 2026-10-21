import { Routes } from '@angular/router';

import { BriefPageComponent } from './features/brief/brief-page.component';

// The Brief loads eagerly (first screen); the other pages are lazy chunks.
export const routes: Routes = [
  { path: '', component: BriefPageComponent, title: 'Morning Brief · FrogRadar' },
  { path: 'signals', loadComponent: () => import('./features/signals/signals-page.component').then((m) => m.SignalsPageComponent), title: 'Signals · FrogRadar' },
  { path: 'battlecards', loadComponent: () => import('./features/battlecards/battlecards-redirect.component').then((m) => m.BattlecardsRedirectComponent), title: 'Battlecards · FrogRadar' },
  { path: 'battlecards/:slug', loadComponent: () => import('./features/battlecards/battlecard-page.component').then((m) => m.BattlecardPageComponent), title: 'Battlecard · FrogRadar' },
  { path: 'radar', loadComponent: () => import('./features/radar/radar-page.component').then((m) => m.RadarPageComponent), title: 'Competitor Radar · FrogRadar' },
  { path: 'radar/:id', loadComponent: () => import('./features/radar/radar-detail-page.component').then((m) => m.RadarDetailPageComponent), title: 'Radar candidate · FrogRadar' },
  { path: 'ask', loadComponent: () => import('./features/ask/ask-page.component').then((m) => m.AskPageComponent), title: 'Ask · FrogRadar' },
  { path: 'missions', loadComponent: () => import('./features/missions/missions-page.component').then((m) => m.MissionsPageComponent), title: 'Missions · FrogRadar' },
  { path: 'missions/:id', loadComponent: () => import('./features/missions/mission-detail-page.component').then((m) => m.MissionDetailPageComponent), title: 'Mission · FrogRadar' },
  {
    path: 'review',
    loadComponent: () => import('./features/review/review-page.component').then((m) => m.ReviewPageComponent),
    title: 'Review queue · FrogRadar',
  },
  { path: '**', redirectTo: '' },
];
