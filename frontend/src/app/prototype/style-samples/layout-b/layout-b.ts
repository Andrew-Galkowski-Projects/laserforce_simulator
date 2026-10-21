import { Component, input } from '@angular/core';
import { SampleScreen } from '../fixture/sample-fixture';
import { PlaceholderLayout } from '../placeholder/placeholder-layout';

/** Layout B -- LFstats-like. Placeholder: its agent replaces this folder wholesale. */
@Component({
  selector: 'app-layout-b',
  imports: [PlaceholderLayout],
  template: `<app-placeholder-layout label="Layout B: LFstats-like" [screen]="screen()" />`,
  styleUrl: './layout-b.scss',
})
export class LayoutB {
  readonly screen = input<SampleScreen>('round');
}
