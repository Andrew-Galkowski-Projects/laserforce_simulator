import { Component, input } from '@angular/core';
import { SampleScreen } from '../fixture/sample-fixture';
import { PlaceholderLayout } from '../placeholder/placeholder-layout';

/** Layout D -- Esports broadcast. Placeholder: its agent replaces this folder wholesale. */
@Component({
  selector: 'app-layout-d',
  imports: [PlaceholderLayout],
  template: `<app-placeholder-layout label="Layout D: Esports broadcast" [screen]="screen()" />`,
  styleUrl: './layout-d.scss',
})
export class LayoutD {
  readonly screen = input<SampleScreen>('round');
}
