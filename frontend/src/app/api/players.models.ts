// Player shapes, snake_case exactly as teams/serializers.py and teams/forms.py
// send / bind them (no mapping layer).
import { ListParams } from './api-client';

/** `teams.models.ROLE_CHOICES` keys. */
export type Role = 'commander' | 'heavy' | 'scout' | 'medic' | 'ammo';

/** The 19 gameplay stats (0-100). Note the capital `O` in `Offensive_synergy`. */
export interface PlayerStats {
  player_awareness: number;
  game_awareness: number;
  resource_awareness: number;
  decision_making: number;
  positioning: number;
  stamina: number;
  speed: number;
  flexibility: number;
  adaptability: number;
  communication: number;
  teamwork: number;
  Offensive_synergy: number;
  defensive_synergy: number;
  midfield_synergy: number;
  resupply_synergy: number;
  resupply_efficiency: number;
  accuracy: number;
  survival: number;
  special_usage: number;
}

/** `PlayerSerializer`: the full Player (detail, list, Team detail `players`). */
export interface Player extends PlayerStats {
  id: number;
  /** The Team's id. */
  team: number;
  /** The Team's name (ANG-06-8: the Players list shows it). */
  team_name: string;
  name: string;
  preferred_roles: Role[];
  /** The profile fields (read-only here; written through `PlayerInput`). */
  age: number | null;
  started_playing_age: number | null;
  total_games: number;
  home_site: string;
  height: string;
  /** `Player.overall_rating`: the unweighted mean of the 19 stats. */
  overall_rating: number;
}

/** `PlayerInlineSerializer`: the slim Player inside a Team list row. */
export interface PlayerInline {
  id: number;
  name: string;
  preferred_roles: Role[];
}

/**
 * `PlayerForm` fields. Every key is optional: on add-player an omitted field
 * takes the HTML `player_add` initial; on PATCH it keeps the current value.
 * Edit Player sends a blank `total_games` or Stat as `null`; the server answers
 * with a field error for it.
 */
export interface PlayerInput extends Partial<Record<keyof PlayerStats, number | null>> {
  name?: string;
  preferred_roles?: Role[];
  age?: number | null;
  started_playing_age?: number | null;
  total_games?: number | null;
  home_site?: string;
  height?: string;
}

/**
 * `PlayerFilterSet` plus `ordering`. `ordering` takes the LG-00c sort keys
 * (`name`, `team`, `overall_rating`, `potential`, each stat in lower case, so
 * `offensive_synergy`); a `-` prefix descends.
 */
export interface PlayerListParams extends ListParams {
  team?: number;
  name?: string;
  ordering?: string;
}

/** `BenchmarkParamsForm`: an omitted key takes the server default (threshold 5, mean). */
export interface CareerParams extends ListParams {
  threshold?: number;
  display?: BenchmarkDisplay;
}

export type BenchmarkDisplay = 'mean' | 'median';

/** The six career-average metrics (`teams/career_stats.py`). */
export interface CareerAverages {
  games: number;
  avg_points: number;
  tag_ratio: number;
  avg_survival_ticks: number;
  avg_accuracy_pct: number;
  avg_sp_earned: number;
}

/** One stat's comparison with its role's population; null values when no one qualifies. */
export interface StatBenchmark {
  benchmark_mean: number | null;
  benchmark_median: number | null;
  delta_mean: number | null;
  delta_median: number | null;
  percentile: number | null;
  qualified: boolean;
  n: number;
}

/** A row of a per-role career table: `benchmark` is null when the stat has no benchmark. */
export interface CareerStatRow {
  key: string;
  label: string;
  player_value: number;
  benchmark: StatBenchmark | null;
}

/** The career averages for one role, with its stat rows. */
export interface CareerRole extends CareerAverages {
  role: Role;
  stat_rows: CareerStatRow[];
}

/** `GET players/{id}/career/` (`build_career_stats`, HX-01 / HX-01b / HX-02). */
export interface CareerStats {
  player: { id: number; name: string; team: { id: number; name: string } | null };
  total_rounds: number;
  has_rounds: boolean;
  career: CareerAverages;
  /** The per-role averages without stat rows (the screens use `per_role_with_benchmarks`). */
  per_role: (CareerAverages & { role: Role })[];
  per_role_with_benchmarks: CareerRole[];
  /** `[round number, rolling-10 average points]` pairs, oldest first. */
  trend: [number, number][];
  min_rounds: number;
  display: BenchmarkDisplay;
  stat_keys: string[];
}

/** One stat's distribution across a role's qualifying Players. */
export interface BenchmarkRow {
  stat: string;
  mean: number | null;
  median: number | null;
  p25: number | null;
  p75: number | null;
  p90: number | null;
  n: number;
}

/** `GET players/benchmarks/` (`build_role_benchmarks`, HX-02; your own + Unmanaged Rounds). */
export interface RoleBenchmarks {
  min_rounds: number;
  display: BenchmarkDisplay;
  roles: Role[];
  stat_keys: string[];
  benchmarks: Partial<Record<Role, BenchmarkRow[]>>;
  any_data: boolean;
}
