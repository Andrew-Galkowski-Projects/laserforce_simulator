import { TeamBase } from '../../api/teams.models';
import { benchPlayers, slottedPlayerIds } from './roster';

const TEAM: TeamBase = {
  id: 1,
  name: 'Red',
  created_date: '2026-10-21T00:00:00Z',
  wins: 0,
  losses: 0,
  slot_commander: 3,
  slot_heavy: null,
  slot_scout_1: 1,
  slot_scout_2: null,
  slot_medic: null,
  slot_ammo: 2,
  is_valid_roster: false,
  roster_errors: [],
};

describe('roster helpers', () => {
  it('lists slotted player ids in slot order, skipping empty slots', () => {
    expect(slottedPlayerIds(TEAM)).toEqual([3, 1, 2]);
  });

  it('benches only the players in no slot', () => {
    const players = [{ id: 1 }, { id: 4 }, { id: 2 }, { id: 5 }, { id: 3 }];
    expect(benchPlayers(TEAM, players)).toEqual([{ id: 4 }, { id: 5 }]);
  });
});
