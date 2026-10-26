import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Stub for `/rounds/:id/heatmap`: ANG-06-10 ticket #386 replaces it with the Movement heatmap. */
@Component({
  selector: 'app-round-heatmap',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<h1>Heatmap</h1>',
})
export class RoundHeatmap {}
