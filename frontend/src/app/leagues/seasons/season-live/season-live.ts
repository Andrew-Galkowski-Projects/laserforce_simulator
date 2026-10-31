import { Component, computed, inject, linkedSignal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { Playback } from '../../../matches/playback/playback';
import { LeagueContext } from '../../stats-shared/league-context';
import { SeasonContext } from '../season-context';

/** `?rounds=<id>` or `?rounds=<id>,<id>`: one game Round, or a two-Round game. */
const ROUNDS = /^\d+(,\d+)?$/;

/**
 * `/leagues/:id/seasons/:seasonId/live?rounds=<id>[,<id>]` (spec #454): One Week (Live), ported
 * from `seasons/play_week_live.html`. Replays the manager's just-played game with the embedded
 * Playback; a two-Round game (a playoff or member night) toggles Round 1 / Round 2. The game is
 * already final: there is nothing to commit, only "Back to dashboard".
 */
@Component({
  selector: 'app-season-live',
  imports: [MatAnchor, Playback, RouterLink],
  templateUrl: './season-live.html',
  styleUrl: './season-live.scss',
})
export class SeasonLive {
  protected readonly context = inject(SeasonContext);
  protected readonly nav = inject(LeagueContext).nav;

  /** The `rounds` query param's Round ids; empty when missing or malformed. */
  protected readonly roundIds = toSignal(
    inject(ActivatedRoute).queryParamMap.pipe(
      map((params) => {
        const rounds = params.get('rounds') ?? '';
        return ROUNDS.test(rounds) ? rounds.split(',').map(Number) : [];
      }),
    ),
    { initialValue: [] },
  );

  /** The toggled Round's index: Round 1 whenever the Rounds change. */
  protected readonly selected = linkedSignal({ source: this.roundIds, computation: () => 0 });

  protected readonly roundId = computed(() => this.roundIds()[this.selected()]);
}
