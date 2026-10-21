import { Component, input } from '@angular/core';
import { SampleScreen } from '../fixture/sample-fixture';
import { PlaceholderLayout } from '../placeholder/placeholder-layout';

/** Layout C -- Clean Material. Placeholder: its agent replaces this folder wholesale. */
@Component({
  selector: 'app-layout-c',
  imports: [PlaceholderLayout],
  template: `<app-placeholder-layout label="Layout C: Clean Material" [screen]="screen()" />`,
  styleUrl: './layout-c.scss',
})
export class LayoutC {
  readonly screen = input<SampleScreen>('round');
}
