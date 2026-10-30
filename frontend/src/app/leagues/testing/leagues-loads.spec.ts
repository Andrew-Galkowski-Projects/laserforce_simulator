import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, firstValueFrom } from 'rxjs';
import { LeaguePlayersService } from '../../api/league-players.service';
import { LeagueTeamService } from '../../api/league-team.service';
import { LeaguesService } from '../../api/leagues.service';
import { releaseLeagueTeamPlayerLoads, releaseLeaguesLoads } from './leagues-loads';

describe('leagues-loads (spec #428 F9)', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  /** Sends the six ANG-06-16 screens' GETs for League 4 (Team 3, Player 25). */
  function sendScreenLoads(): void {
    const teams = TestBed.inject(LeagueTeamService);
    const players = TestBed.inject(LeaguePlayersService);
    const page = { page: 1, page_size: 10 };
    const loads: Observable<unknown>[] = [
      teams.roster(4, 3),
      teams.schedule(4, 3),
      teams.history(4, 3),
      teams.historyPlayers(4, 3, page),
      players.freeAgents(4, page),
      players.watchList(4, page),
      players.player(4, 25),
    ];
    for (const load of loads) void firstValueFrom(load).catch(() => undefined);
  }

  it('releaseLeagueTeamPlayerLoads releases the team and player screen GETs only', () => {
    sendScreenLoads();
    void firstValueFrom(TestBed.inject(LeaguesService).nav(4)).catch(() => undefined);
    void firstValueFrom(TestBed.inject(LeaguePlayersService).watch(4, 25)).catch(() => undefined);
    releaseLeagueTeamPlayerLoads();
    // The layout's nav and a Watch flag PUT are the spec's own business.
    const left = http.match(() => true).map((r) => `${r.request.method} ${r.request.url}`);
    expect(left).toHaveLength(2);
    expect(left[0]).toMatch(/^GET .*\/leagues\/4\/nav\/$/);
    expect(left[1]).toMatch(/^PUT .*\/leagues\/4\/watch-list\/25\/$/);
  });

  it('releaseLeaguesLoads still releases every League GET, the new screens included', () => {
    sendScreenLoads();
    releaseLeaguesLoads();
    http.verify();
  });
});
