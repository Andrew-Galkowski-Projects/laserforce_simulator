import { Component } from '@angular/core';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';

/** `/leagues/:id/seasons/:seasonId/awards` (spec #444). A stub until its ticket (#448). */
@Component({
  selector: 'app-season-awards',
  imports: [SeasonBreadcrumb],
  template: `<app-season-breadcrumb />
    <h1>Awards</h1>`,
})
export class SeasonAwards {}
