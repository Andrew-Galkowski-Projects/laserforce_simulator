import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Stub for `/rounds/:id/missile-log`: ANG-06-10 ticket #385 replaces it with the Missile log. */
@Component({
  selector: 'app-missile-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<h1>Missile log</h1>',
})
export class MissileLog {}
