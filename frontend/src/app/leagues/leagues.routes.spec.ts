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

    // Routing only: drop whatever each screen loads, so the screens' own tickets (#412-#414)
    // never edit this file.
    afterEach(() => http.match(() => true));

    it.each([
      ['/leagues', LeagueList, 'Leagues - Laserforce Simulator'],
      ['/leagues/new', LeagueCreate, 'Create League - Laserforce Simulator'],
      ['/leagues/new/advanced', LeagueCreateAdvanced, 'Advanced League - Laserforce Simulator'],
      ['/leagues/4', LeagueDashboard, 'League - Laserforce Simulator'],
      ['/leagues/4/history', LeagueHistory, 'League History - Laserforce Simulator'],
      ['/leagues/4/new-team', LeagueNewTeam, 'Choose New Team - Laserforce Simulator'],
    ])('resolves %s to its screen and page title', async (url, screen, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });

    it.each(['/leagues/4', '/leagues/4/history', '/leagues/4/new-team'])(
      'puts %s inside the League layout',
      async (url) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(harness.fixture.debugElement.query(By.directive(LeagueLayout))).not.toBeNull();
      },
    );

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
