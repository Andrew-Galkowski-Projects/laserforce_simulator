import { InjectionToken } from '@angular/core';
import fixtureJson from './style-sample-fixture.json';

/**
 * ANG-04b data seam (spec #246). One real simulated round, captured by
 * `scripts/prototype/capture_style_sample_fixture.py`. Layouts read it through `SAMPLE_FIXTURE`
 * only; they never import the JSON.
 */
export type Side = 'red' | 'blue';
export type RoleCode = 'CMD' | 'HVY' | 'SCT' | 'AMO' | 'MED';
/** One per `--lf-ev-*` token. */
export type SampleEventType =
  'elimination' | 'missile' | 'special' | 'nuke' | 'miss' | 'resupply' | 'tag';
export type SampleScreen = 'teams' | 'round' | 'timeline';
export const SAMPLE_SCREENS: readonly SampleScreen[] = ['teams', 'round', 'timeline'];

export interface SampleTeamPlayer {
  name: string;
  preferredRoles: RoleCode[];
}

export interface SampleTeam {
  id: number;
  name: string;
  wins: number;
  losses: number;
  players: SampleTeamPlayer[];
}

export interface SamplePlayerState {
  playerName: string;
  side: Side;
  role: RoleCode;
  points: number;
  /** From `PlayerRoundState.get_mvp()`. Styled as a link, but inert. */
  mvp: number;
  tagsMade: number;
  timesTagged: number;
  /** tagsMade / timesTagged (lfstats "Hit Diff"). */
  hitDiff: number;
  /** Shot accuracy, 0-100. */
  accuracy: number;
  medicHits: number;
  /** Lives left at the end of the round. */
  lives: number;
  startingLives: number;
  shotsFired: number;
  /** lfstats "Msls". */
  missilesLanded: number;
  /** lfstats "Msld". */
  timesMissiled: number;
  specialsUsed: number;
  resuppliesGiven: number;
  /** Tick (0.5 s) of elimination, or null if the player survived. */
  eliminatedAtTick: number | null;
  /** 0..900; the lfstats "Survived" bar is survivedSeconds / 900. */
  survivedSeconds: number;
}

export interface SampleRound {
  roundNumber: number;
  redTeamName: string;
  blueTeamName: string;
  redPoints: number;
  bluePoints: number;
  redEliminated: boolean;
  blueEliminated: boolean;
  /** Bonus the surviving Side earns per eliminated opponent (not included in the points). */
  eliminationBonus: number;
  eliminatedAtTick: number | null;
  winner: Side | null;
  /** Red first, then blue; each Side sorted by points, highest first. */
  playerStates: SamplePlayerState[];
}

export interface SampleEvent {
  /** Ticks into the round (1 tick = 0.5 s); show as m:ss via `tickToClock`. */
  tick: number;
  type: SampleEventType;
  /** The raw `GameEvent.event_type` (tag, locking, missiled, resupply_ammo, ...). */
  rawType: string;
  actorName: string;
  actorSide: Side;
  targetName: string | null;
  targetSide: Side | null;
  points: number;
  description: string;
}

export interface StyleSampleFixture {
  capturedFrom: { seed: number; generatorSeed: number; eventCountTotal: number };
  teams: SampleTeam[];
  round: SampleRound;
  /** ~40 events around the first elimination, plus the first of any type the window missed. */
  events: SampleEvent[];
}

export const SAMPLE_FIXTURE = new InjectionToken<StyleSampleFixture>('SAMPLE_FIXTURE', {
  providedIn: 'root',
  factory: () => fixtureJson as StyleSampleFixture,
});

/** Tick (0.5 s) to `m:ss`. */
export function tickToClock(tick: number): string {
  const seconds = Math.floor(tick / 2);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
