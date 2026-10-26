import { RoundHeatmap } from '../../api/rounds.models';
import { HeatmapFilter, filteredOccupancy } from './heatmap-filter';

/** Ace (red CMD), Bolt (blue CMD), Cleo (red SCT), Dax (blue SCT); Eve (red MED) never moved. */
function heatmap(): RoundHeatmap {
  return {
    round_id: 9,
    has_map: true,
    arena_map: { id: 7, name: 'Arena' },
    zone_size: 40,
    processed_image_url: null,
    cell_occupancy_json: {
      '1': { '0,0': 2, '1,1': 3 },
      '2': { '1,1': 5 },
      '3': { '0,0': 7, '2,2': 1 },
      '4': { '2,2': 11 },
    },
    player_roster: [
      { id: 1, name: 'Ace', role: 'commander', team_color: 'red' },
      { id: 3, name: 'Cleo', role: 'scout', team_color: 'red' },
      { id: 5, name: 'Eve', role: 'medic', team_color: 'red' },
      { id: 2, name: 'Bolt', role: 'commander', team_color: 'blue' },
      { id: 4, name: 'Dax', role: 'scout', team_color: 'blue' },
    ],
  };
}

const ALL: HeatmapFilter = { playerId: null, role: null, team: null };

describe('filteredOccupancy', () => {
  it('sums every roster player per Cell when nothing is filtered', () => {
    expect(filteredOccupancy(heatmap(), ALL)).toEqual({ '0,0': 9, '1,1': 8, '2,2': 12 });
  });

  it('keeps one player', () => {
    expect(filteredOccupancy(heatmap(), { ...ALL, playerId: 3 })).toEqual({ '0,0': 7, '2,2': 1 });
  });

  it('keeps one role', () => {
    expect(filteredOccupancy(heatmap(), { ...ALL, role: 'commander' })).toEqual({
      '0,0': 2,
      '1,1': 8,
    });
  });

  it('keeps one Side', () => {
    expect(filteredOccupancy(heatmap(), { ...ALL, team: 'blue' })).toEqual({
      '1,1': 5,
      '2,2': 11,
    });
  });

  it('combines the filters (AND)', () => {
    expect(filteredOccupancy(heatmap(), { ...ALL, role: 'scout', team: 'blue' })).toEqual({
      '2,2': 11,
    });
  });

  it('is empty when the filters match nobody', () => {
    expect(filteredOccupancy(heatmap(), { playerId: 1, role: 'scout', team: null })).toEqual({});
  });

  it('is empty for a roster player with no occupancy entry', () => {
    expect(filteredOccupancy(heatmap(), { ...ALL, playerId: 5 })).toEqual({});
  });

  it('ignores occupancy of a player not on the roster', () => {
    const h = heatmap();
    h.cell_occupancy_json['99'] = { '0,0': 100 };
    expect(filteredOccupancy(h, ALL)).toEqual({ '0,0': 9, '1,1': 8, '2,2': 12 });
  });
});
