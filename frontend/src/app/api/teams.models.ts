// Team shapes, snake_case exactly as teams/serializers.py and teams/forms.py
// send / bind them (no mapping layer).
import { ListParams } from './api-client';
import { Player, PlayerInline } from './players.models';

/** The fields `TeamListSerializer` and `TeamSerializer` share. Slots are Player ids. */
export interface TeamBase {
  id: number;
  name: string;
  /** ISO 8601 datetime. */
  created_date: string;
  wins: number;
  losses: number;
  slot_commander: number | null;
  slot_heavy: number | null;
  slot_scout_1: number | null;
  slot_scout_2: number | null;
  slot_medic: number | null;
  slot_ammo: number | null;
}

/** `TeamListSerializer`: a row of `GET teams/`. */
export interface TeamListItem extends TeamBase {
  players: PlayerInline[];
}

/** `TeamSerializer`: `GET teams/{id}/` and every Team write's response. */
export interface Team extends TeamBase {
  players: Player[];
}

/** `TeamForm` fields (create; PATCH sends any subset). */
export interface TeamInput {
  name: string;
}

/** `TeamFilterSet` plus `ordering` (name, created_date, wins, losses, id; `-` descends). */
export interface TeamListParams extends ListParams {
  name?: string;
  include_free_agents?: boolean;
  ordering?: string;
}
