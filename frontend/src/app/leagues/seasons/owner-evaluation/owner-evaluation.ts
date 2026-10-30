import { Component } from '@angular/core';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';

/**
 * `/leagues/:id/seasons/:seasonId/owner-evaluation` (spec #444). A stub until its ticket (#449).
 * The `OwnerEvaluation` model in `api/seasons.models.ts` shares this name: import it aliased.
 */
@Component({
  selector: 'app-owner-evaluation',
  imports: [SeasonBreadcrumb],
  template: `<app-season-breadcrumb />
    <h1>Annual Performance Evaluation</h1>`,
})
export class OwnerEvaluation {}
