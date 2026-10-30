import { Component } from '@angular/core';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';

/** `/leagues/:id/seasons/:seasonId/schedule` (spec #444). A stub until its ticket (#447). */
@Component({
  selector: 'app-season-schedule',
  imports: [SeasonBreadcrumb],
  template: `<app-season-breadcrumb />
    <h1>Schedule</h1>`,
})
export class SeasonSchedule {}
