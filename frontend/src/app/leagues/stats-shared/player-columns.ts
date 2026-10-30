// The League Player tables' columns and cell formats (spec #428 F7), shared so no screen copies
// them:
// - Player Ratings' columns (`RATING_*`): Player Ratings and Free Agents (the same row,
//   `PlayerRatingRow`). Headers sort by `key` and take `full` as their `title`.
// - Player Stats' stat columns (`STAT_COLUMNS`, after Name / Team / GP): Player Stats and Watch
//   List (`PlayerStatRow`). A cell is `statCell(row.stats, column)`.
// - `potentialText` / `orDash` / `truncateChars`: the templates' `floatformat:1` + `default:"—"`,
//   `default:"-"` and `truncatechars:n`.

/** A sortable Player Ratings header: the API sort key, its abbreviation and its full name. */
export interface SortColumn {
  key: string;
  label: string;
  full: string;
}

/** `RATING_SORT_KEYS_DISPLAY` (`league_views.py`), in its order. */
export const RATING_SORT_COLUMNS: readonly SortColumn[] = [
  { key: 'name', label: 'Name', full: 'Name' },
  { key: 'team', label: 'Team', full: 'Team' },
  { key: 'preferred_roles', label: 'Roles', full: 'Preferred Roles' },
  { key: 'overall_rating', label: 'Ovr', full: 'Overall' },
  { key: 'player_awareness', label: 'PlAw', full: 'Player Awareness' },
  { key: 'game_awareness', label: 'GmAw', full: 'Game Awareness' },
  { key: 'resource_awareness', label: 'RsAw', full: 'Resource Awareness' },
  { key: 'decision_making', label: 'Dec', full: 'Decision Making' },
  { key: 'positioning', label: 'Pos', full: 'Positioning' },
  { key: 'stamina', label: 'Sta', full: 'Stamina' },
  { key: 'speed', label: 'Spd', full: 'Speed' },
  { key: 'flexibility', label: 'Flx', full: 'Flexibility' },
  { key: 'adaptability', label: 'Adp', full: 'Adaptability' },
  { key: 'communication', label: 'Comm', full: 'Communication' },
  { key: 'teamwork', label: 'Team', full: 'Teamwork' },
  { key: 'offensive_synergy', label: 'OffSyn', full: 'Offensive Synergy' },
  { key: 'defensive_synergy', label: 'DefSyn', full: 'Defensive Synergy' },
  { key: 'midfield_synergy', label: 'MidSyn', full: 'Midfield Synergy' },
  { key: 'resupply_synergy', label: 'RsupSyn', full: 'Resupply Synergy' },
  { key: 'resupply_efficiency', label: 'RsupEff', full: 'Resupply Efficiency' },
  { key: 'accuracy', label: 'Acc', full: 'Accuracy' },
  { key: 'survival', label: 'Surv', full: 'Survival' },
  { key: 'special_usage', label: 'SpcUse', full: 'Special Usage' },
  { key: 'potential', label: 'Pot', full: 'Potential' },
];

/** The 19 rating stats: the sortable columns between Ovr and Pot (`row.stats` keys). */
export const RATING_STAT_KEYS: readonly string[] = RATING_SORT_COLUMNS.slice(4, -1).map(
  (c) => c.key,
);

/** The non-sortable bio headers after Pot (MMR and Rank are dropped, STAT-PROXY-01). */
export const RATING_BIO_HEADERS = [
  { key: 'age', label: 'Age', full: 'Age' },
  { key: 'home_site', label: 'Site', full: 'Home Site' },
  { key: 'height', label: 'Ht', full: 'Height' },
  { key: 'games', label: 'GP', full: 'Games Played' },
  { key: 'started', label: 'Start', full: 'Started Playing Age' },
] as const;

/**
 * A Player Stats stat column: its sort key (the API's, and the `stats` key), header, decimal
 * places, and suffix.
 */
export interface StatColumn {
  key: string;
  label: string;
  places: number;
  suffix?: string;
}

/** `_PLAYER_STATS_COLUMNS` after Name / Team / GP, with `player_stats.html`'s `floatformat`s. */
export const STAT_COLUMNS: readonly StatColumn[] = [
  { key: 'points_scored', label: 'Points', places: 0 },
  { key: 'mvp', label: 'MVP', places: 1 },
  { key: 'tags_made', label: 'Tags', places: 0 },
  { key: 'times_tagged', label: 'Tagged', places: 0 },
  { key: 'tag_ratio', label: 'Tag Ratio', places: 2 },
  { key: 'accuracy', label: 'Acc%', places: 1 },
  { key: 'survival', label: 'Survival', places: 0, suffix: 's' },
  { key: 'final_lives', label: 'Lives', places: 0 },
  { key: 'resupplies_given', label: 'Resup', places: 0 },
  { key: 'missiles_landed', label: 'Missiles', places: 0 },
  { key: 'specials_used', label: 'Specials', places: 0 },
  { key: 'follow_up_shots', label: 'Follow-up', places: 0 },
  { key: 'reaction_shots', label: 'Reaction', places: 0 },
  { key: 'combo_resupply_count', label: 'Combo Resup', places: 0 },
];

/** One stat cell: `column.places` decimals plus its suffix; a missing stat shows as 0. */
export function statCell(stats: Record<string, number>, column: StatColumn): string {
  return (stats[column.key] ?? 0).toFixed(column.places) + (column.suffix ?? '');
}

/** Potential to one decimal place, or "—" when the Player has none. */
export function potentialText(potential: number | null): string {
  return potential === null ? '—' : potential.toFixed(1);
}

/** `default:"-"`: a missing number or an empty string shows "-". */
export function orDash(value: number | string | null): string {
  return value === null || value === '' ? '-' : String(value);
}

/** Django's `truncatechars:n`: `n` characters at most, the last an ellipsis when cut. */
export function truncateChars(value: string, n: number): string {
  return value.length > n ? `${value.slice(0, n - 1)}…` : value;
}
