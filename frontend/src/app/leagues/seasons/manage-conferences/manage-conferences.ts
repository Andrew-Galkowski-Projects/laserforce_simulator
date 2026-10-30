import { Component } from '@angular/core';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';

/** `/leagues/:id/seasons/:seasonId/conferences` (spec #444). A stub until its ticket (#450). */
@Component({
  selector: 'app-manage-conferences',
  imports: [SeasonBreadcrumb],
  template: `<app-season-breadcrumb />
    <h1>Manage Conferences</h1>`,
})
export class ManageConferences {}
