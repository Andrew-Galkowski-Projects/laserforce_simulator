// Tournament detail fixtures. Imported only by *.spec.ts files.
import { TournamentDetail } from '../../api/tournaments.models';

/**
 * A `build_tournament_detail` payload: a sandbox preset single-elimination Tournament in setup
 * with two participants, shaped as `GET tournaments/{id}/` sends it.
 */
export function tournamentDetail(overrides: Partial<TournamentDetail> = {}): TournamentDetail {
  return {
    id: 5,
    name: 'Autumn Cup',
    format: 'single_elimination',
    format_label: 'Single elimination',
    state: 'setup',
    team_assembly: 'preset',
    role_assignment_mode: 'random',
    champion: null,
    embedded: false,
    season: null,
    final_series_length: 1,
    semifinal_series_length: 1,
    quarterfinal_series_length: 1,
    earlier_series_length: 1,
    wb_advancers: 0,
    lb_advancers: 0,
    swiss_rounds: 0,
    manager: 1,
    created_at: '2026-10-27T09:05:00Z',
    participants: [
      { team: { id: 11, name: 'Red Raiders' }, seed: 1 },
      { team: { id: 12, name: 'Blue Bolts' }, seed: 2 },
    ],
    pool_size: 0,
    is_drawn: false,
    stage: 'setup',
    brackets: [],
    rr_standings: [],
    swiss_standings: [],
    cut_labels: {},
    rr_crosstable: [],
    can_play: false,
    ...overrides,
  };
}
