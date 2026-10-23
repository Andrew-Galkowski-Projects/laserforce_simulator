import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { Match, MatchType } from '../../api/matches.models';
import { MatchesService } from '../../api/matches.service';
import { RecordNotFound } from '../shared/record-not-found';
import { matchResult } from '../shared/result';
import { ResultChip } from '../shared/result-chip';
import { loadRouteRecord } from '../shared/route-record';

/** One Round row of the Match Summary, by team position (team_red / team_blue), not colour. */
interface RoundRow {
  label: string;
  redPoints: number;
  bluePoints: number;
  /** True when team_red eliminated team_blue that Round (so team_red took the elim bonus). */
  redBonus: boolean;
  blueBonus: boolean;
  /** The team with more points that Round, or "Tie". */
  winner: string;
  /** The stored GameRound's id, or null when there is none ("No round data"). */
  roundId: number | null;
  /** False for Round 2 of an in-progress Match (a League Match after Round 1). */
  played: boolean;
}

/** `/matches/:id`: the Match Summary (parity with Django's `enhanced_match_detail.html`). */
@Component({
  selector: 'app-match-detail',
  imports: [DatePipe, DecimalPipe, MatAnchor, RecordNotFound, ResultChip, RouterLink],
  templateUrl: './match-detail.html',
  styleUrl: './match-detail.scss',
})
export class MatchDetail {
  private readonly matches = inject(MatchesService);

  protected readonly load = loadRouteRecord('Match', (id) => this.matches.get(id));
  protected readonly result = matchResult;
  /** `Match.MATCH_TYPES` display names, as the Django page shows them. */
  protected readonly matchTypeLabels: Record<MatchType, string> = {
    tournament: 'Tournament Match',
    friendly: 'Friendly Match',
  };

  protected rounds(m: Match): RoundRow[] {
    // The Match columns are keyed by team position: `red_round2_*` is team_red's Round 2 (when it
    // plays blue), and `blue_roundN_eliminated` means team_red eliminated team_blue. The server
    // orders `round_ids` by round_number, so `round_ids[0]` is Round 1.
    return [
      this.roundRow(m, 1, m.red_round1_points, m.blue_round1_points, {
        red: m.blue_round1_eliminated,
        blue: m.red_round1_eliminated,
      }),
      this.roundRow(m, 2, m.red_round2_points, m.blue_round2_points, {
        red: m.blue_round2_eliminated,
        blue: m.red_round2_eliminated,
      }),
    ];
  }

  private roundRow(
    m: Match,
    roundNumber: number,
    redPoints: number,
    bluePoints: number,
    bonus: { red: boolean; blue: boolean },
  ): RoundRow {
    const winner =
      redPoints > bluePoints ? m.team_red_name : bluePoints > redPoints ? m.team_blue_name : 'Tie';
    return {
      label: `Round ${roundNumber}`,
      redPoints,
      bluePoints,
      redBonus: bonus.red,
      blueBonus: bonus.blue,
      winner,
      roundId: m.round_ids[roundNumber - 1] ?? null,
      played: roundNumber === 1 || m.is_completed,
    };
  }
}
