// Player Pool spec fixtures. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';
import { Player } from '../../api/players.models';
import { PROFILE, STATS } from '../../api/testing/player-fixtures';
import { PoolEntry } from '../../api/tournaments.models';

/** A `pool_entry_payload` row: a Player on `team` (null: teamless), undrawn unless overridden. */
export function poolEntry(
  id: number,
  name: string,
  overrides: Partial<PoolEntry> = {},
  team: { id: number; name: string } | null = { id: 40, name: 'Free Agents' },
): PoolEntry {
  return { player: { id, name, team }, tier: null, drawn_team: null, ...overrides };
}

/** A `GET players/` row for the add-existing picker. */
export function poolPlayer(id: number, name: string, teamName = 'Free Agents'): Player {
  return {
    id,
    team: 40,
    name,
    preferred_roles: [],
    ...PROFILE,
    team_name: teamName,
    ...STATS,
  };
}

/** One page of a list envelope, as `listAll` reads it. */
export function poolPage<T>(items: T[], page = 1, totalPages = 1) {
  return {
    data: items,
    meta: { page, page_size: 100, count: items.length, total_pages: totalPages },
  };
}

/**
 * Releases the GETs the Player Pool section sends when it loads (the pool and the add-existing
 * picker's Players), for specs that land on a Player-pool Tournament but test something else.
 */
export function releasePoolLoads(): void {
  const base = environment.apiBaseUrl;
  TestBed.inject(HttpTestingController).match(
    (req) =>
      req.method === 'GET' &&
      ((req.url.startsWith(`${base}/tournaments/`) && req.url.endsWith('/pool/')) ||
        req.url === `${base}/players/`),
  );
}
