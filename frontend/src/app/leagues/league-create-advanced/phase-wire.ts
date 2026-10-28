// The Advanced create form's phase composer, as data: one `PhaseRow` per block, serialised to
// the `phases` wire string `create_advanced.html`'s `serialize()` builds (:442-496).
import { RrdeCombo, SeriesLength, TournamentFormat } from '../../api/tournaments.models';
import { PhaseType, TournamentMode } from '../../api/leagues.models';

/** The round-robin schedule formats a composer row offers. */
export type PhaseScheduleFormat = 'single_round_robin' | 'double_round_robin';

/** The tournament modes a composer row may pick (`worlds` is derived, never authored). */
export type ComposerTournamentMode = Exclude<TournamentMode, 'worlds'>;

/**
 * One composer row. Like the template's selects, a row keeps every sub-config value while it is
 * hidden, and the serialiser decides which ones the token carries.
 */
export interface PhaseRow {
  type: PhaseType;
  scheduleFormat: PhaseScheduleFormat;
  mode: ComposerTournamentMode;
  /** The top-N participant cut; null when the input is emptied. */
  cut: number | null;
  format: TournamentFormat;
  finalSl: SeriesLength;
  semiSl: SeriesLength;
  quarterSl: SeriesLength;
  earlierSl: SeriesLength;
  combo: RrdeCombo;
  /** null when the input is emptied. */
  swissRounds: number | null;
}

/** A new row: the defaults `buildRow()` gives its selects and inputs (:214-439). */
export function newPhaseRow(): PhaseRow {
  return {
    type: 'round_robin',
    scheduleFormat: 'single_round_robin',
    mode: 'standings',
    cut: 0,
    format: 'single_elimination',
    finalSl: 1,
    semiSl: 1,
    quarterSl: 1,
    earlierSl: 1,
    combo: '4/0',
    swissRounds: 0,
  };
}

/** The comma-joined tokens, in row order; no rows gives `''` (:495). */
export function phaseWire(rows: readonly PhaseRow[]): string {
  return rows.map(phaseToken).join(',');
}

function phaseToken(row: PhaseRow): string {
  // :453-456
  if (row.type === 'round_robin') return `round_robin:${row.scheduleFormat}`;
  // :457-459: a bare token.
  if (row.type === 'member_night') return 'member_night';
  // :477-485: the advancers only for RR -> DE, else 0 / 0.
  const [wb, lb] = row.format === 'round_robin_double_elim' ? row.combo.split('/') : ['0', '0'];
  // :463-492: the positional "tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss".
  return [
    'tournament',
    row.mode,
    row.cut ?? 0,
    row.format,
    row.finalSl,
    row.semiSl,
    row.quarterSl,
    row.earlierSl,
    wb,
    lb,
    row.swissRounds ?? 0,
  ].join(':');
}
