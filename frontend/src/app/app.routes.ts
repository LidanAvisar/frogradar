import { Routes } from '@angular/router';

import { BriefPageComponent } from './features/brief/brief-page.component';
import { PlaceholderPageComponent } from './features/placeholder/placeholder-page.component';

const soon = (path: string, heading: string, icon: string, description: string) => ({
  path,
  component: PlaceholderPageComponent,
  title: `${heading} · FrogRadar`,
  data: { heading, icon, description },
});

export const routes: Routes = [
  { path: '', component: BriefPageComponent, title: 'Morning Brief · FrogRadar' },
  soon('signals', 'Signals', 'signals', 'Browse, filter and search every collected signal, with sources and evidence.'),
  soon('battlecards', 'Battlecards', 'battlecards', 'Compare JFrog with each competitor: capability matrix, strengths, gaps and talking points.'),
  soon('ask', 'Ask', 'ask', 'Ask questions about the market and get answers that cite their sources.'),
  soon('missions', 'Missions', 'missions', 'Give a research agent a task and follow its steps until the report is ready.'),
  soon('radar', 'Competitor Radar', 'radar', 'New players nobody configured yet: emergence score, a verified profile, and approve or dismiss.'),
  soon('review', 'Review queue', 'review', 'Approve, reject or correct new insights; corrections teach the pipeline.'),
  { path: '**', redirectTo: '' },
];
