import { Component, inject } from '@angular/core';
import { RoundsService } from '../../api/rounds.service';
import { RecordNotFound } from '../shared/record-not-found';
import { roundResult } from '../shared/result';
import { ResultSummary } from '../shared/result-summary';
import { loadRouteRecord } from '../shared/route-record';

/** `/rounds/:id`: a Single Round's result summary until ANG-06 item 4's Round page. */
@Component({
  selector: 'app-round-detail',
  imports: [RecordNotFound, ResultSummary],
  template: `
    @if (load.notFound()) {
      <app-record-not-found noun="Single Round" />
    } @else if (load.loadError(); as message) {
      <p class="stub__error" role="alert">{{ message }}</p>
    } @else if (load.record(); as r) {
      <app-result-summary
        [redName]="r.team_red_name"
        [blueName]="r.team_blue_name"
        [redScore]="r.red_points"
        [blueScore]="r.blue_points"
        [result]="result(r)"
        [datePlayed]="r.date_played"
      />
    }
  `,
  styleUrl: '../shared/stub.scss',
})
export class RoundDetail {
  private readonly rounds = inject(RoundsService);

  protected readonly load = loadRouteRecord('Round', (id) => this.rounds.get(id));
  protected readonly result = roundResult;
}
