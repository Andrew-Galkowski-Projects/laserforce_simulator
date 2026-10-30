import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, firstValueFrom } from 'rxjs';
import { LeagueFinancesService } from '../../api/league-finances.service';
import { LeaguePlayersService } from '../../api/league-players.service';
import { LeagueTeamService } from '../../api/league-team.service';
import { LeaguesService } from '../../api/leagues.service';
import { teamFinancesUpdate } from './league-finances-fixtures';
import { releaseLeagueTeamPlayerLoads, releaseLeaguesLoads } from './leagues-loads';

describe('leagues-loads (spec #428 F9, spec #439)', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  /**
   * Sends the six ANG-06-16 screens' GETs and the two ANG-06-17 finance GETs for League 4
   * (Team 3, Player 25).
   */
  function sendScreenLoads(): void {
    const teams = TestBed.inject(LeagueTeamService);
    const players = TestBed.inject(LeaguePlayersService);
    const finances = TestBed.inject(LeagueFinancesService);
    const page = { page: 1, page_size: 10 };
    const loads: Observable<unknown>[] = [
      teams.roster(4, 3),
      teams.schedule(4, 3),
      teams.history(4, 3),
      teams.historyPlayers(4, 3, page),
      players.freeAgents(4, page),
      players.watchList(4, page),
      players.player(4, 25),
      finances.league(4),
      finances.team(4, 3),
    ];
    for (const load of loads) void firstValueFrom(load).catch(() => undefined);
  }

  it('releaseLeagueTeamPlayerLoads releases the team, player and finance screen GETs only', () => {
    sendScreenLoads();
    void firstValueFrom(TestBed.inject(LeaguesService).nav(4)).catch(() => undefined);
    void firstValueFrom(TestBed.inject(LeaguePlayersService).watch(4, 25)).catch(() => undefined);
    void firstValueFrom(
      TestBed.inject(LeagueFinancesService).updateTeam(4, 3, teamFinancesUpdate()),
    ).catch(() => undefined);
    releaseLeagueTeamPlayerLoads();
    // The layout's nav, a Watch flag PUT and a finances PATCH are the spec's own business.
    const left = http.match(() => true).map((r) => `${r.request.method} ${r.request.url}`);
    expect(left).toHaveLength(3);
    expect(left[0]).toMatch(/^GET .*\/leagues\/4\/nav\/$/);
    expect(left[1]).toMatch(/^PUT .*\/leagues\/4\/watch-list\/25\/$/);
    expect(left[2]).toMatch(/^PATCH .*\/leagues\/4\/teams\/3\/finances\/$/);
  });

  it('releaseLeaguesLoads still releases every League GET, the new screens included', () => {
    sendScreenLoads();
    releaseLeaguesLoads();
    http.verify();
  });
});
