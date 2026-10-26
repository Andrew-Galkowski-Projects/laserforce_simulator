// The Movement heatmap's pure part: a Round's per-player Cell occupancy -> one summed map.
import { CellKey } from '../../api/maps.models';
import { RoundHeatmap } from '../../api/rounds.models';

/** The three pickers; `null` keeps everyone. */
export interface HeatmapFilter {
  playerId: number | null;
  role: string | null;
  team: 'red' | 'blue' | null;
}

/**
 * Per Cell, the summed occupancy of every roster player passing all three filters (AND), as the
 * Django template's script did. A player with no occupancy entry adds nothing.
 */
export function filteredOccupancy(
  heatmap: RoundHeatmap,
  filter: HeatmapFilter,
): Record<CellKey, number> {
  const sums: Record<CellKey, number> = {};
  for (const player of heatmap.player_roster) {
    if (filter.playerId !== null && player.id !== filter.playerId) continue;
    if (filter.role !== null && player.role !== filter.role) continue;
    if (filter.team !== null && player.team_color !== filter.team) continue;
    const cells = heatmap.cell_occupancy_json[String(player.id)] ?? {};
    for (const [key, value] of Object.entries(cells)) {
      sums[key] = (sums[key] ?? 0) + value;
    }
  }
  return sums;
}
