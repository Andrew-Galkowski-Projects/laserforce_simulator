import { TestBed } from '@angular/core/testing';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonContext } from './season-context';

describe('SeasonContext (spec #444)', () => {
  function context(leagueId: number | null, seasonId: number | null): SeasonContext {
    TestBed.configureTestingModule({ providers: [LeagueContext, SeasonContext] });
    TestBed.inject(LeagueContext).leagueId.set(leagueId);
    const ctx = TestBed.inject(SeasonContext);
    ctx.seasonId.set(seasonId);
    return ctx;
  }

  describe('link()', () => {
    it('is the Season dashboard with no screen', () => {
      expect(context(1, 11).link()).toEqual(['/leagues', 1, 'seasons', 11]);
    });

    it('appends the screen segments', () => {
      const ctx = context(1, 11);
      expect(ctx.link('standings')).toEqual(['/leagues', 1, 'seasons', 11, 'standings']);
      expect(ctx.link('a', 'b')).toEqual(['/leagues', 1, 'seasons', 11, 'a', 'b']);
    });

    it('throws outside a loaded Season layout (no Season id yet)', () => {
      expect(() => context(1, null).link('awards')).toThrowError(/Season id/);
    });
  });

  describe('requireSeasonId() / requireLeagueId()', () => {
    it('return the ids once the layouts have set them', () => {
      const ctx = context(1, 11);
      expect(ctx.requireSeasonId()).toBe(11);
      expect(ctx.requireLeagueId()).toBe(1);
    });

    it('throw while an id is still unset', () => {
      expect(() => context(null, 11).requireLeagueId()).toThrowError(/League id/);
      TestBed.resetTestingModule();
      expect(() => context(1, null).requireSeasonId()).toThrowError(/Season id/);
    });
  });
});
