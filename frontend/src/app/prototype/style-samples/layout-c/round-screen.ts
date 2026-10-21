import { DecimalPipe } from '@angular/common';
import { Component, computed, inject } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { SAMPLE_FIXTURE, Side } from '../fixture/sample-fixture';
import { SideTableC } from './side-table';

interface SideSummary {
  side: Side;
  label: string;
  teamName: string;
  points: number;
  bonus: number;
  eliminated: boolean;
  result: 'Win' | 'Loss' | 'Draw';
}

/** Layout C Round detail: a score summary card and a sortable `mat-table` per Side. */
@Component({
  selector: 'app-lc-round',
  imports: [DecimalPipe, MatCardModule, MatChipsModule, SideTableC],
  template: `
    <h1 class="lc-h1">Round {{ round.roundNumber }}</h1>
    <div class="lc-summary-grid">
      @for (s of summaries(); track s.side) {
        <mat-card appearance="outlined" class="lc-summary" [attr.data-lf-side]="s.side">
          <div class="lc-summary__band">{{ s.label }} side</div>
          <mat-card-header>
            <mat-card-title>{{ s.teamName }}</mat-card-title>
            <mat-card-subtitle>
              @if (s.eliminated) {
                Eliminated
              } @else {
                Survived
              }
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content class="lc-summary__body">
            <div class="lc-summary__score">
              <span class="lc-num">{{ s.points + s.bonus | number }}</span>
              @if (s.bonus) {
                <span class="lc-summary__bonus"
                  >{{ s.points | number }} + {{ s.bonus | number }} elimination bonus</span
                >
              }
            </div>
            <mat-chip-set>
              <mat-chip
                class="lc-result"
                [class.lc-result--win]="s.result === 'Win'"
                [class.lc-result--loss]="s.result === 'Loss'"
                >{{ s.result }}</mat-chip
              >
            </mat-chip-set>
          </mat-card-content>
        </mat-card>
      }
    </div>

    @for (s of summaries(); track s.side) {
      <section class="lc-side-section" [attr.data-lf-side]="s.side">
        <h2 class="lc-h2">
          <span class="lc-side-tag">{{ s.label }}</span> {{ s.teamName }}
        </h2>
        <app-lc-side-table [rows]="rowsFor(s.side)" [topMvp]="topMvp()" />
      </section>
    }
  `,
})
export class RoundScreenC {
  private readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly round = this.fixture.round;

  protected readonly topMvp = computed(() =>
    Math.max(...this.round.playerStates.map((p) => p.mvp)),
  );

  protected readonly summaries = computed<SideSummary[]>(() => {
    const r = this.round;
    const make = (side: Side): SideSummary => {
      const red = side === 'red';
      const opponentEliminated = red ? r.blueEliminated : r.redEliminated;
      return {
        side,
        label: red ? 'Red' : 'Blue',
        teamName: red ? r.redTeamName : r.blueTeamName,
        points: red ? r.redPoints : r.bluePoints,
        bonus: opponentEliminated ? r.eliminationBonus : 0,
        eliminated: red ? r.redEliminated : r.blueEliminated,
        result: r.winner === null ? 'Draw' : r.winner === side ? 'Win' : 'Loss',
      };
    };
    return [make('red'), make('blue')];
  });

  protected rowsFor(side: Side) {
    return this.round.playerStates.filter((p) => p.side === side);
  }
}
