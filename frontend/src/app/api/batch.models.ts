// `/api/simulate-batch/` (API-03, ANG-01b): a Batch run job and its Save-games job.

/** `POST simulate-batch/`: `arena_map` null is the 3-zone fallback. */
export interface BatchRunInput {
  team_red: number;
  team_blue: number;
  n: number;
  arena_map: number | null;
}

/** The started Batch run: the job id plus the matchup it runs, as the server resolved it. */
export interface BatchRunStarted {
  job_id: string;
  team_red_id: number;
  team_red_name: string;
  team_blue_id: number;
  team_blue_name: string;
  arena_map_id: number | null;
  n: number;
}

/** The SIM-10 Job status vocabulary. `running` covers a pending, unknown or expired job id. */
export type JobStatus = 'running' | 'complete' | 'error';

/**
 * One game's replay key, `[RNG seed, flipped]`. The seed is a 64-bit integer that JSON parsing
 * rounds, so the client only counts these pairs: the server reads the real ones from the job.
 */
export type SeedPair = [number, boolean];

/** The raw physical-Side signal across the batch (the per-team keys are side-neutral). */
export interface SideAdvantage {
  n: number;
  red_side_wins: number;
  blue_side_wins: number;
  side_ties: number;
  red_side_win_pct: number;
  blue_side_win_pct: number;
  avg_red_side_score: number;
  avg_blue_side_score: number;
}

/** A Batch run's aggregate, team-position keyed: `red_*` is the run's `team_red`. */
export interface BatchAggregate {
  n: number;
  red_wins: number;
  blue_wins: number;
  ties: number;
  red_win_pct: number;
  blue_win_pct: number;
  avg_red_score: number;
  avg_blue_score: number;
  avg_red_survivors: number;
  avg_blue_survivors: number;
  red_scores: number[];
  blue_scores: number[];
  /** The Average games. */
  avg_seeds: SeedPair[];
  /** The Highlight games. */
  outlier_seeds: SeedPair[];
  side_advantage: SideAdvantage;
}

/** `GET simulate-batch/{job_id}/`: `partial` is null until the job first reports progress. */
export interface BatchRunStatus {
  status: JobStatus;
  completed: number;
  total: number;
  partial: BatchAggregate | null;
  error: string | null;
  team_red_id: number | null;
  team_blue_id: number | null;
  arena_map_id: number | null;
}

/** `POST simulate-batch/{job_id}/save/`: `avg` saves Average games, `outlier` Highlight games. */
export interface SaveGamesInput {
  team_red: number;
  team_blue: number;
  arena_map: number | null;
  game_type: 'avg' | 'outlier';
  /** 1 to 10. */
  n: number;
}

/** `GET simulate-batch/save/{save_job_id}/`. */
export interface SaveGamesStatus {
  status: JobStatus;
  error: string | null;
  round_ids: number[];
}
