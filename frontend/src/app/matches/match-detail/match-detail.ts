import { Component, inject } from '@angular/core';
import { MatchesService } from '../../api/matches.service';
import { RecordNotFound } from '../shared/record-not-found';
import { matchResult } from '../shared/result';
import { ResultSummary } from '../shared/result-summary';
import { loadRouteRecord } from '../shared/route-record';

/** `/matches/:id`: a small result summary until ANG-06 item 4's Match page. */
@Component({
  selector: 'app-match-detail',
  imports: [RecordNotFound, ResultSummary],
  template: `
    @if (load.notFound()) {
      <app-record-not-found noun="Match" />
    } @else if (load.loadError(); as message) {
      <p class="stub__error" role="alert">{{ message }}</p>
    } @else if (load.record(); as m) {
      <app-result-summary
        [redName]="m.team_red_name"
        [blueName]="m.team_blue_name"
        [redScore]="m.red_total_points"
        [blueScore]="m.blue_total_points"
        [result]="result(m)"
        [datePlayed]="m.date_played"
      />
    }
  `,
  styleUrl: '../shared/stub.scss',
})
export class MatchDetail {
  private readonly matches = inject(MatchesService);

  protected readonly load = loadRouteRecord('Match', (id) => this.matches.get(id));
  protected readonly result = matchResult;
}
