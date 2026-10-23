// Round scoreboard spec fixtures (ANG-06d). Imported only by *.spec.ts files.
import { RoundScoreboard, ScoreboardRow, ScoreboardSide } from '../../api/rounds.models';

/** A surviving red scout: 1,200 points, 10 tags / 4 tagged, 50% accuracy, no missiles. */
export function scoreboardRow(overrides: Partial<ScoreboardRow> = {}): ScoreboardRow {
  return {
    player_id: 101,
    name: 'Ace',
    role: 'scout',
    team_color: 'red',
    was_eliminated_at: 1801,
    eliminated_timestamp: '',
    is_eliminated: false,
    final_lives: 5,
    points_scored: 1200,
    mvp: 4.5,
    tags_made: 10,
    times_tagged: 4,
    accuracy: 50,
    final_shots: 20,
    final_special: 3,
    shots_used: 20,
    missiles_used: 0,
    starting_missiles: 0,
    missiles_landed: 0,
    times_missiled: 0,
    final_medic_hits: 1,
    medic_lives_removed_from_nuke: 0,
    follow_up_shots: 0,
    reaction_shots: 0,
    resupplies_given: 0,
    specials_used: 1,
    combo_resupply_count: 0,
    specific_tags_count: 2,
    special_cost: 10,
    ...overrides,
  };
}

/** One Side with `players`; survivors counted from them, no bonus, not eliminated by default. */
export function scoreboardSide(
  players: ScoreboardRow[] = [],
  overrides: Partial<ScoreboardSide> = {},
): ScoreboardSide {
  return {
    players,
    survivors: players.filter((p) => p.final_lives > 0).length,
    eliminated: false,
    elim_bonus: 0,
    ...overrides,
  };
}

/** A scoreboard with no rows on either Side. */
export function emptyScoreboard(): RoundScoreboard {
  return { red: scoreboardSide(), blue: scoreboardSide() };
}
