import { Component } from '@angular/core';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';

/** `/leagues/:id/seasons/:seasonId/standings` (spec #444). A stub until its ticket (#446). */
@Component({
  selector: 'app-season-standings',
  imports: [SeasonBreadcrumb],
  template: `<app-season-breadcrumb />
    <h1>Standings</h1>`,
})
export class SeasonStandings {}
