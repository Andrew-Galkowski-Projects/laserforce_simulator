import { Component, input } from '@angular/core';
import { SampleScreen } from '../fixture/sample-fixture';
import { PlaceholderLayout } from '../placeholder/placeholder-layout';

/** Layout A -- Dense dashboard. Placeholder: its agent replaces this folder wholesale. */
@Component({
  selector: 'app-layout-a',
  imports: [PlaceholderLayout],
  template: `<app-placeholder-layout label="Layout A: Dense dashboard" [screen]="screen()" />`,
  styleUrl: './layout-a.scss',
})
export class LayoutA {
  readonly screen = input<SampleScreen>('round');
}
