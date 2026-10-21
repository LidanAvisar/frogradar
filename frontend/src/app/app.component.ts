import { ChangeDetectionStrategy, Component } from '@angular/core';

import { AppShellComponent } from './layout/app-shell.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<fr-app-shell />',
})
export class AppComponent {}
