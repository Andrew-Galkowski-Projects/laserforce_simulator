// GameRound / PlayerRoundState / GameEvent shapes, snake_case exactly as
// matches/serializers.py and matches/forms.py send / bind them (no mapping layer).
// Ticks: 1 tick = 0.5 s, a round is 0-1800; 1801 means "survived".
import { ListParams } from './api-client';
import { CellKey } from './maps.models';
import { Role } from './players.models';

/** The physical Side a team or player played (`team_color`). */
export type Side = 'red' | 'blue';

/** `GameRoundListSerializer`: a row of `GET rounds/`. Foreign keys are ids. */
export interface GameRoundListItem {
  id: number;
  /** null for a standalone Round. */
  match: number | null;
  round_number: number;
  team_red: number | null;
  team_blue: number | null;
  /** ISO 8601 datetime. */
  date_played: string;
  red_points: number;
  blue_points: number;
  red_team_eliminated: boolean;
  blue_team_eliminated: boolean;
  /** Tick of the team elimination; 1801 if neither team was eliminated. */
  eliminated_at: number;
  winner: number | null;
  is_completed: boolean;
  team_red_name: string;
  team_blue_name: string;
  /** null when `winner` is null (a tie). */
  winner_name: string | null;
}

/** `PlayerRoundStateSerializer` (every model field but `game_round`). */
export interface PlayerRoundState {
  id: number;
  /** The Player's id. */
  player: number;
  team_color: Side;
  role: string;
  starting_lives: number;
  starting_shots: number;
  starting_special: number;
  starting_missiles: number;
  /** `PlayerRoundState.tag_id` choice (0-15). */
  last_tagged_id: number;
  shields: number;
  last_downed_time: number | null;
  neutral_base_destroyed: boolean;
  opposing_base_destroyed: boolean;
  /** 0 = red zone, 1 = neutral, 2 = blue zone. */
  zone_fallback: number;
  cell_row: number | null;
  cell_col: number | null;
  special_active_until: number | null;
  is_hiding: boolean;
  final_lives: number;
  final_shots: number;
  final_special: number;
  final_missiles: number;
  final_medic_hits: number;
  points_scored: number;
  tags_made: number;
  shots_missed: number;
  times_tagged: number;
  specials_used: number;
  own_specials_cancelled: number;
  enemy_nuke_cancels: number;
  ally_nuke_cancels: number;
  medic_lives_removed_from_nuke: number;
  lives_lost_to_nukes: number;
  missiles_landed: number;
  times_missiled: number;
  resupplies_given: number;
  combo_resupply_count: number;
  times_tagged_in_reset_window: number;
  follow_up_shots: number;
  reaction_shots: number;
  ticks_active: number;
  ticks_not_targetable: number;
  ticks_reset_window: number;
  missile_points: number;
  /** Per-opponent counts keyed by Player id. */
  specific_tags: Record<string, unknown>;
  was_eliminated_at: number;
}

/**
 * One **Round scoreboard** row (`matches/services.py` `_player_row`): the 28
 * `round_summary.PLAYER_ROW_KEYS` plus `player_id`.
 */
export interface ScoreboardRow {
  player_id: number;
  name: string;
  role: Role;
  team_color: Side;
  /** Ticks; 1801 (or 0, legacy) means survived; null on legacy rows (then `final_lives` decides). */
  was_eliminated_at: number | null;
  /** `m:ss` when eliminated, else `''`. */
  eliminated_timestamp: string;
  is_eliminated: boolean;
  final_lives: number;
  points_scored: number;
  /** The SM5 MVP score (2 decimals). */
  mvp: number;
  tags_made: number;
  times_tagged: number;
  /** Integer percentage, 0-100. */
  accuracy: number;
  final_shots: number;
  final_special: number;
  shots_used: number;
  missiles_used: number;
  starting_missiles: number;
  missiles_landed: number;
  times_missiled: number;
  final_medic_hits: number;
  medic_lives_removed_from_nuke: number;
  follow_up_shots: number;
  reaction_shots: number;
  resupplies_given: number;
  specials_used: number;
  combo_resupply_count: number;
  specific_tags_count: number;
  special_cost: number;
}

/** One Side of `round_scoreboard`: rows ordered `-points_scored, role, name`. */
export interface ScoreboardSide {
  players: ScoreboardRow[];
  survivors: number;
  /** True when no player on this Side has lives left. */
  eliminated: boolean;
  /** The elimination bonus this Side earned (the other Side was eliminated), else 0. */
  elim_bonus: number;
}

/** `GameRoundSerializer.scoreboard`, grouped by the Side each player played. */
export type RoundScoreboard = Record<Side, ScoreboardSide>;

/** `GameRoundSerializer`: `GET rounds/{id}/` and the create response. */
export interface GameRound extends GameRoundListItem {
  player_states: PlayerRoundState[];
  scoreboard: RoundScoreboard;
}

/** `GameEvent.EVENT_TYPES` keys. */
export type GameEventType =
  | 'tag'
  | 'locking'
  | 'missiled'
  | 'special'
  | 'miss'
  | 'resupply_ammo'
  | 'resupply_lives'
  | 'elimination'
  | 'team_elimination'
  | 'nuke_cancelled'
  | 'medic_reset';

/** `GameEventSerializer` (every model field but `game_round`): a row of `GET rounds/{id}/events/`. */
export interface GameEvent {
  id: number;
  /** Ticks into the round. */
  timestamp: number;
  event_type: GameEventType;
  points_awarded: number;
  description: string;
  metadata: Record<string, unknown>;
  /** The acting Player's id. */
  actor: number;
  /** The target Player's id, if any. */
  target: number | null;
}

/**
 * Every `event_type` the engine emits: the 11 `GameEvent.EVENT_TYPES` keys
 * plus the four emitted but unlisted ones. The `timeline/` vocabulary.
 */
export type TimelineEventType =
  GameEventType | 'movement' | 'base_capture' | 'combo_resupply' | 'missile_dodge';

/** One **Event log** row of `GET rounds/{id}/timeline/` (`matches/services.py` `round_timeline`). */
export interface TimelineEvent {
  /** Ticks into the round (1 tick = 0.5 s). */
  tick: number;
  event_type: TimelineEventType;
  points: number;
  description: string;
  actor_id: number;
  actor_name: string;
  actor_side: Side;
  /** The three `target_*` fields are all null when the event has no target. */
  target_id: number | null;
  target_name: string | null;
  target_side: Side | null;
  /** `{}` when the event stored none. */
  metadata: Record<string, unknown>;
}

/** A player of the Round, in `PlayerRoundState.id` order. */
export interface TimelinePlayer {
  id: number;
  name: string;
  side: Side;
  role: string;
}

/** The six RV-02 **Highlight** kinds (`sim_helpers/highlights.py` `KINDS`). */
export type HighlightKind =
  | 'nuke_detonation'
  | 'nuke_cancelled'
  | 'medic_reset'
  | 'first_elimination'
  | 'team_elimination'
  | 'scoring_burst';

/** A **Highlight** record (`GameRound.highlights_json`), passed through unchanged. */
export interface Highlight {
  kind: HighlightKind;
  /** Ticks into the round. */
  tick: number | null;
  team: Side | null;
  /** Player names, not ids. */
  actor: string | null;
  target: string | null;
  points: number | null;
  label: string;
}

/** `GameRound.get_event_summary()`. */
export interface EventSummary {
  total_events: number;
  tags: number;
  moves: number;
  missiles: number;
  misses: number;
  eliminations: number;
  resupplies: number;
  specials: number;
}

/** `GET rounds/{id}/timeline/`: the whole Event log, unpaginated. */
export interface RoundTimeline {
  /** Every event, movement included, in `(timestamp, id)` order. */
  events: TimelineEvent[];
  players: TimelinePlayer[];
  /** `[]` on a round from before RV-02. */
  highlights: Highlight[];
  summary: EventSummary;
}

/**
 * One **Playback snapshot**: a player's live state from `tick` until their next
 * snapshot. Read on the server from the logged after-event values (ADR-0044).
 */
export interface PlaybackSnapshot {
  tick: number;
  score: number;
  lives: number;
  shots: number;
  /** Can read −1 while MECH-16 spends a hit missile twice. */
  missiles: number;
  sp: number;
  tags: number;
  misses: number;
  /** The tick of the player's latest Down or received resupply; `null` before either. */
  downed_at: number | null;
  eliminated: boolean;
  rapid_fire: boolean;
  nuking: boolean;
}

/** A player's snapshots: a tick-0 opening one, then one per tick their state changed. */
export interface PlaybackPlayer {
  id: number;
  snapshots: PlaybackSnapshot[];
}

/** One player's recorded route on the beta Playback map. */
export interface PlaybackMapPlayer {
  id: number;
  name: string;
  team: Side;
  role: string;
  /** 1 / 2 for a Side's two Scouts, else 0. */
  scout: number;
  /** `[row, col]`. */
  spawn: [number, number];
  /** `[tick, polyline]`: the cells walked that Advance, ending at the end cell. */
  moves: [number, [number, number][]][];
}

/** The beta map payload (`build_playback_map`); `null` for a map-less Round. */
export interface PlaybackMap {
  zone_size: number;
  /** Fetch the image from `maps/{id}/processed-image/`. */
  arena_map: { id: number; name: string };
  players: PlaybackMapPlayer[];
}

/** `GET rounds/{id}/playback/`: the Round's Playback snapshots per player, plus the map. */
export interface RoundPlayback {
  /** 1800. */
  duration_ticks: number;
  /** 8. */
  not_targetable_ticks: number;
  /** 16. */
  respawn_ticks: number;
  /** In `PlayerRoundState.id` order, as `RoundTimeline.players`. */
  players: PlaybackPlayer[];
  map: PlaybackMap | null;
}

/** One **Missiled event** row of the Missile log (`MissileRow`; roles only, no player names). */
export interface MissileLogRow {
  /** Ticks. */
  timestamp: number;
  /** The tick as `MM:SS`, formatted by the server. */
  timestamp_mmss: string;
  actor_role: string;
  target_role: string;
  /** `'hit'` or `'miss'`. */
  result: string;
  friendly_fire: boolean;
  description: string;
  points: number;
  /** `'missile-row'`, plus `' friendly-fire'` on a Friendly-fire row. */
  row_class: string;
}

/** `GET rounds/{id}/missile-log/`: the summary and every Missiled event in time order. */
export interface RoundMissileLog {
  round_id: number;
  fired: number;
  /** Friendly-fire hits included. */
  hit: number;
  /** `hit / fired * 100`, unrounded; 0 when nothing was fired. */
  efficiency: number;
  rows: MissileLogRow[];
}

/** One player of the heatmap's roster: red Side first, then by `(role, name)`. */
export interface HeatmapRosterEntry {
  id: number;
  name: string;
  role: string;
  team_color: Side;
}

/** `GET rounds/{id}/heatmap/`: the Round's **Cell occupancy** per player, plus its map. */
export interface RoundHeatmap {
  round_id: number;
  has_map: boolean;
  /** Fetch the image from `maps/{id}/processed-image/`; `null` for a map-less Round. */
  arena_map: { id: number; name: string } | null;
  zone_size: number | null;
  /** The session-only Django URL: never fetched by the SPA (use `arena_map.id`). */
  processed_image_url: string | null;
  /** Player id (as a string) -> `"r,c"` -> ticks; `{}` when nothing was recorded. */
  cell_occupancy_json: Record<string, Record<CellKey, number>>;
  player_roster: HeatmapRosterEntry[];
}

/**
 * `SingleRoundSetupForm` fields. Teams must be the caller's own valid rosters;
 * `arena_map` must have a confirmed zone config (omit for the 3-zone fallback).
 */
export interface RoundInput {
  team_red: number;
  team_blue: number;
  arena_map?: number | null;
}

/** `GameRoundFilterSet` plus `ordering` (`date_played`, `round_number`, `id`; `-` descends). */
export interface RoundListParams extends ListParams {
  match?: number;
  arena_map?: number;
  /** true: only Single Rounds (no Match); false: only a Match's Rounds. */
  single_round?: boolean;
  ordering?: string;
}

/**
 * `GameEventFilterSet`. Events always come in `(timestamp, id)` order;
 * `timestamp_min` / `timestamp_max` are inclusive ticks.
 */
export interface GameEventListParams extends ListParams {
  event_type?: GameEventType;
  actor?: number;
  target?: number;
  timestamp_min?: number;
  timestamp_max?: number;
}
