import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By, Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { signIn } from './api/testing/auth-fixtures';
import { releaseTeamsLoads } from './teams/testing/teams-loads';
import { routes } from './app.routes';
import { authInterceptor } from './auth/auth.interceptor';
import { MatchDetail } from './matches/match-detail/match-detail';
import { MatchList } from './matches/match-list/match-list';
import { RoundDetail } from './matches/round-detail/round-detail';
import { SetupForm } from './matches/setup-form/setup-form';
import { releaseMatchesLoads } from './matches/testing/matches-loads';
import { RosterImport } from './teams/roster-import/roster-import';
import { TeamCreate } from './teams/team-create/team-create';
import { TeamDetail } from './teams/team-detail/team-detail';
import { TeamEdit } from './teams/team-edit/team-edit';
import { TeamGenerate } from './teams/team-generate/team-generate';
import { TeamList } from './teams/team-list/team-list';
import { TeamSlots } from './teams/team-slots/team-slots';

describe('app routes', () => {
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

  afterEach(() => {
    releaseTeamsLoads();
    releaseMatchesLoads();
    http.verify();
  });

  const routerUrl = (): string => TestBed.inject(Router).url;

  describe('signed out', () => {
    it('sends a deep link to /login with a returnUrl', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/teams/5/edit');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams%2F5%2Fedit');
    });

    it('sends the Add and Edit Player deep links to /login with a returnUrl', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/teams/5/players/new');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams%2F5%2Fplayers%2Fnew');
      await harness.navigateByUrl('/teams/5/players/9/edit');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams%2F5%2Fplayers%2F9%2Fedit');
    });

    it('sends the app root to /login with /teams as the returnUrl', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams');
    });

    it('shows the sign-in page at /login', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/login');
      expect(routerUrl()).toBe('/login');
      expect(TestBed.inject(Title).getTitle()).toBe('Sign in - Laserforce Simulator');
    });
  });

  describe('signed in', () => {
    beforeEach(async () => {
      await signIn();
    });

    it('redirects the app root to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      expect(routerUrl()).toBe('/teams');
    });

    it('redirects an unknown path to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/no-such-page');
      expect(routerUrl()).toBe('/teams');
    });

    it('sends a visit to /login on to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/login?returnUrl=%2Fteams%2F3');
      expect(routerUrl()).toBe('/teams');
    });

    it.each([
      ['/teams', TeamList, 'Teams - Laserforce Simulator'],
      ['/teams/new', TeamCreate, 'Create Team - Laserforce Simulator'],
      ['/teams/3', TeamDetail, 'Team - Laserforce Simulator'],
      ['/teams/3/edit', TeamEdit, 'Edit Team - Laserforce Simulator'],
      ['/teams/3/slots', TeamSlots, 'Assign Slots - Laserforce Simulator'],
    ])('resolves %s to its Teams screen and page title', async (url, screen, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });

    it.each([
      ['/teams/generate', TeamGenerate, 'Generate Players'],
      ['/teams/import', RosterImport, 'Import Roster'],
    ])('resolves %s to its bulk screen, not to Team detail', async (url, screen, heading) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(TeamDetail))).toBeNull();
      expect(harness.routeNativeElement?.querySelector('h1')?.textContent?.trim()).toBe(heading);
      expect(TestBed.inject(Title).getTitle()).toBe(`${heading} - Laserforce Simulator`);
    });
  });

  describe('signed in, Matches area', () => {
    beforeEach(async () => {
      await signIn();
    });

    /** The `data` of the deepest activated route (what the routed screen reads). */
    function leafData(): Record<string, unknown> {
      let route = TestBed.inject(Router).routerState.snapshot.root;
      while (route.firstChild) route = route.firstChild;
      return route.data;
    }

    it.each([
      ['/matches', MatchList, 'Match History - Laserforce Simulator'],
      ['/matches/7', MatchDetail, 'Match - Laserforce Simulator'],
      ['/rounds/9', RoundDetail, 'Round - Laserforce Simulator'],
    ])('resolves %s to its screen and page title', async (url, screen, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.fixture.debugElement.query(By.directive(screen))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });

    it.each([
      ['/matches/new', 'match', MatchDetail, 'New Match - Laserforce Simulator'],
      ['/rounds/new', 'round', RoundDetail, 'New Single Round - Laserforce Simulator'],
    ])(
      'resolves %s to the setup form (kind %s), not to a detail page',
      async (url, kind, detail, title) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(routerUrl()).toBe(url);
        expect(harness.fixture.debugElement.query(By.directive(SetupForm))).not.toBeNull();
        expect(harness.fixture.debugElement.query(By.directive(detail))).toBeNull();
        expect(leafData()['kind']).toBe(kind);
        expect(TestBed.inject(Title).getTitle()).toBe(title);
      },
    );
  });

  describe('signed out, Matches area', () => {
    it.each(['/matches', '/matches/new', '/matches/7', '/rounds/new', '/rounds/9'])(
      'sends %s to /login with a returnUrl',
      async (url) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(routerUrl()).toBe(`/login?returnUrl=${encodeURIComponent(url)}`);
      },
    );
  });

  describe('signed out, bulk screens', () => {
    it.each(['/teams/generate', '/teams/import'])(
      'sends %s to /login with a returnUrl',
      async (url) => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl(url);
        expect(routerUrl()).toBe(`/login?returnUrl=${encodeURIComponent(url)}`);
      },
    );
  });
});
