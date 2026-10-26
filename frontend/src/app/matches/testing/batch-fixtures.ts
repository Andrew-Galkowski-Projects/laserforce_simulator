// Batch run fixtures (ANG-06-12), shaped as `/api/simulate-batch/` sends them.
import { BatchAggregate, BatchRunStarted, BatchRunStatus } from '../../api/batch.models';

export const STARTED: BatchRunStarted = {
  job_id: 'job-1',
  team_red_id: 1,
  team_red_name: 'Alpha',
  team_blue_id: 2,
  team_blue_name: 'Bravo',
  arena_map_id: null,
  n: 10,
};

/** A 10-game aggregate: Alpha 7 W, Bravo 2 W, 1 tie. */
export function aggregate(overrides: Partial<BatchAggregate> = {}): BatchAggregate {
  return {
    n: 10,
    red_wins: 7,
    blue_wins: 2,
    ties: 1,
    red_win_pct: 70,
    blue_win_pct: 20,
    avg_red_score: 31186.8,
    avg_blue_score: 27307.2,
    avg_red_survivors: 3.4,
    avg_blue_survivors: 1.9,
    red_scores: [32490, 4000],
    blue_scores: [23031, 12000],
    // Real seeds are 64-bit (beyond a JS number); the screen only counts them.
    avg_seeds: [[11, false]],
    outlier_seeds: [[12, true]],
    side_advantage: {
      n: 10,
      red_side_wins: 6,
      blue_side_wins: 3,
      side_ties: 1,
      red_side_win_pct: 60,
      blue_side_win_pct: 30,
      avg_red_side_score: 30500.4,
      avg_blue_side_score: 28000.6,
    },
    ...overrides,
  };
}

/** A status poll answer; `running` with no aggregate by default. */
export function runStatus(overrides: Partial<BatchRunStatus> = {}): BatchRunStatus {
  return {
    status: 'running',
    completed: 0,
    total: 10,
    partial: null,
    error: null,
    team_red_id: null,
    team_blue_id: null,
    arena_map_id: null,
    ...overrides,
  };
}
