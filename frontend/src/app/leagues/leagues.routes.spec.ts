import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By, Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { signIn } from '../api/testing/auth-fixtures';
import { routes } from '../app.routes';
import { authInterceptor } from '../auth/auth.interceptor';
import { LeagueCreateAdvanced } from './league-create-advanced/league-create-advanced';
import { LeagueCreate } from './league-create/league-create';
import { LeagueDashboard } from './league-dashboard/league-dashboard';
import { LeagueHistory } from './league-history/league-history';
import { LeagueLayout } from './league-layout/league-layout';
import { LeagueList } from './league-list/league-list';
import { LeagueNewTeam } from './league-new-team/league-new-team';
import { GameLog } from './game-log/game-log';
import { LeagueLeaders } from './league-leaders/league-leaders';
import { PlayerRatings } from './player-ratings/player-ratings';
import { PlayerStats } from './player-stats/player-stats';
import { Playoffs } from './playoffs/playoffs';
import { PowerRankings } from './power-rankings/power-rankings';
import { StatisticalFeats } from './statistical-feats/statistical-feats';
import { TeamStats } from './team-stats/team-stats';
import { FreeAgents } from './free-agents/free-agents';
import { LeaguePlayerDetail } from './league-player/league-player';
import { LeagueTeamHistory } from './team-history/team-history';
import { LeagueTeamRoster } from './team-roster/team-roster';
import { LeagueTeamSchedule } from './team-schedule/team-schedule';
import { WatchList } from './watch-list/watch-list';

describe('Leagues routes', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  const routerUrl = (): string => TestBed.inject(Router).url;

  describe('signed in', () => {
    beforeEach(async () => {
      await signIn();
    });

    // Routing only: drop whatever each screen loads (every League request, the stats screens'
    // too), so the screens' own tickets (#412-#414, #420-#425) never edit this file.
    afterEach(() => http.match(() => true));

    it.each([
      ['/leagues', LeagueList, 'Leagues - Laserforce Simulator'],
      ['/leagues/new', LeagueCreate, 'Create League - Laserforce Simulator'],
      ['/leagues/new/advanced', LeagueCreateAdvanced, 'Advanced League - Laserforce Simulator'],
      ['/leagues/4', LeagueDashboard, 'League - Laserforce Simulator'],
      ['/leagues/4/history', LeagueHistory, 'League History - Laserforce Simulator'],
      ['/leagues/4/new-team', LeagueNewTeam, 'Choose New Team - Laserforce Simulator'],
      ['/leagues/4/stats/league-leaders', LeagueLeaders, 'League Leaders - Laserforce Simulator'],
      ['/leagues/4/stats/player-stats', PlayerStats, 'Player Stats - Laserforce Simulator'],
      ['/leagues/4/stats/player-ratings', PlayerRatings, 'Player Ratings - Laserforce Simulator'],
      ['/leagues/4/stats/team-stats', TeamStats, 'Team Stats - Laserforce Simulator'],
      [
        '/leagues/4/stats/statistical-feats',
        StatisticalFeats,
        'Statistical Feats - Laserforce Simulator',
      ],
      ['/leagues/4/stats/game-log', GameLog, 'Game Log - Laserforce Simulator'],
      ['/leagues/4/power-rankings', PowerRankings, 'Power Rankings - Laserforce Simulator'],
      ['/leagues/4/playoffs', Playoffs, 'Playoffs - Laserforce Simulator'],
    ])('resolves %s to its screen and page title', async (url, screen, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });

    it.each([
      '/leagues/4',
      '/leagues/4/history',
      '/leagues/4/new-team',
      '/leagues/4/stats/league-leaders',
      '/leagues/4/stats/player-stats',
      '/leagues/4/stats/player-ratings',
      '/leagues/4/stats/team-stats',
      '/leagues/4/stats/statistical-feats',
      '/leagues/4/stats/game-log',
      '/leagues/4/power-rankings',
      '/leagues/4/playoffs',
    ])('puts %s inside the League layout', async (url) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(harness.fixture.debugElement.query(By.directive(LeagueLayout))).not.toBeNull();
    });

    it.each(['/leagues', '/leagues/new', '/leagues/new/advanced'])(
      'shows %s without the League layout',
      async (url) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(harness.fixture.debugElement.query(By.directive(LeagueLayout))).toBeNull();
      },
    );

    it('never reads /leagues/new as a League id', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/leagues/new');
      expect(harness.fixture.debugElement.query(By.directive(LeagueDashboard))).toBeNull();
    });
  });

  describe('the League team and player screens (spec #428 F6)', () => {
    beforeEach(async () => {
      await signIn();
    });

    // Routing only: drop every load, so the screen tickets (#431-#435) never edit this file.
    afterEach(() => http.match(() => true));

    it.each([
      ['/leagues/4/team/roster', LeagueTeamRoster, 'Roster - Laserforce Simulator'],
      ['/leagues/4/team/schedule', LeagueTeamSchedule, 'Team Schedule - Laserforce Simulator'],
      ['/leagues/4/team/history', LeagueTeamHistory, 'Team History - Laserforce Simulator'],
      ['/leagues/4/players/free-agents', FreeAgents, 'Free Agents - Laserforce Simulator'],
      ['/leagues/4/players/watch-list', WatchList, 'Watch List - Laserforce Simulator'],
      ['/leagues/4/players/25', LeaguePlayerDetail, 'League Player - Laserforce Simulator'],
    ])('resolves %s inside the League layout, with its page title', async (url, screen, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(LeagueLayout))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });

    it('never reads players/free-agents or players/watch-list as a Player id', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/leagues/4/players/watch-list');
      expect(harness.fixture.debugElement.query(By.directive(LeaguePlayerDetail))).toBeNull();
      await harness.navigateByUrl('/leagues/4/players/free-agents');
      expect(harness.fixture.debugElement.query(By.directive(LeaguePlayerDetail))).toBeNull();
    });
  });

  describe('signed out', () => {
    afterEach(() => http.verify());

    it.each(['/leagues', '/leagues/new', '/leagues/4', '/leagues/4/history'])(
      'sends %s to /login with a returnUrl',
      async (url) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(routerUrl()).toBe(`/login?returnUrl=${encodeURIComponent(url)}`);
      },
    );
  });
});
