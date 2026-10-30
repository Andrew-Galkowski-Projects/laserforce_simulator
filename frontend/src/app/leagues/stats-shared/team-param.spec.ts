import { convertToParamMap } from '@angular/router';
import { leagueNav } from '../testing/league-fixtures';
import { resolveTeamParam, teamQueryParam } from './team-param';

describe('teamQueryParam', () => {
  it('reads ?team= as a Team id', () => {
    expect(teamQueryParam(convertToParamMap({ team: '5' }))).toBe(5);
  });

  it('is null when ?team= is missing or not a positive integer', () => {
    expect(teamQueryParam(convertToParamMap({}))).toBeNull();
    for (const bad of ['0', '-3', 'abc', '2.5', '']) {
      expect(teamQueryParam(convertToParamMap({ team: bad }))).toBeNull();
    }
  });
});

describe('resolveTeamParam (spec #428 F8)', () => {
  const nav = leagueNav({ schedule_team: { id: 3, name: 'Red Raiders' } });

  it('takes the ?team= Team when there is one', () => {
    expect(resolveTeamParam(5, nav)).toBe(5);
  });

  it("falls back to the nav's schedule Team (the Team the Account manages)", () => {
    expect(resolveTeamParam(null, nav)).toBe(3);
  });

  it('is null with no ?team= and no schedule Team', () => {
    expect(resolveTeamParam(null, leagueNav({ schedule_team: null }))).toBeNull();
  });

  it('is null before the nav has loaded, unless ?team= names a Team', () => {
    expect(resolveTeamParam(null, null)).toBeNull();
    expect(resolveTeamParam(5, null)).toBe(5);
  });
});
