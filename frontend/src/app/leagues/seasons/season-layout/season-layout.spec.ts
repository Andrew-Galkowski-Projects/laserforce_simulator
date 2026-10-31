import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { leagueNav } from '../../testing/league-fixtures';
import { failure } from '../../testing/league-stats-fixtures';
import { SeasonAwards } from '../season-awards/season-awards';
import { SeasonStandings } from '../season-standings/season-standings';
import { season, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout, releaseSeasonScreenLoads } from '../testing/season-loads';
import { releasePlayControls } from '../../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

describe('Season layout (spec #444 I1 / I2)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => {
    releaseSeasonScreenLoads();
    http.match((req) => req.url.endsWith('/nav/'));
    releasePlayControls();
    http.verify();
  });

  const layout = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector('app-season-layout') as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const linkIn = (el: Element | null, label: string): HTMLAnchorElement | undefined =>
    [...(el?.querySelectorAll('a') ?? [])].find((a) => text(a) === label);
  const buttonIn = (el: Element | null, label: string): HTMLButtonElement | undefined =>
    [...(el?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
  const seasonGets = () =>
    http.match((req) => req.method === 'GET' && req.url === `${BASE}/seasons/11/`);

  async function navigate(path = 'standings'): Promise<void> {
    await harness.navigateByUrl(seasonUrl(path));
    http.match(`${BASE}/leagues/1/nav/`).forEach((r) => r.flush({ data: leagueNav(), meta: {} }));
  }

  it('loads the Season with GET seasons/{seasonId}/ and then renders the screen', async () => {
    await navigate();
    expect(harness.fixture.debugElement.query(By.directive(SeasonStandings))).toBeNull();
    await releaseSeasonLayout(harness);
    expect(harness.fixture.debugElement.query(By.directive(SeasonStandings))).not.toBeNull();
  });

  it('loads the Season once while moving between its screens', async () => {
    await navigate('standings');
    await releaseSeasonLayout(harness);
    await harness.navigateByUrl(seasonUrl('awards'));
    expect(seasonGets()).toHaveLength(0);
    expect(harness.fixture.debugElement.query(By.directive(SeasonAwards))).not.toBeNull();
  });

  it('loads the other Season when the :seasonId changes', async () => {
    await navigate('standings');
    await releaseSeasonLayout(harness);
    await harness.navigateByUrl('/leagues/1/seasons/12/standings');
    await releaseSeasonLayout(harness, season({ id: 12, name: 'Season 2' }));
    expect(text(layout().querySelector('nav[aria-label="Breadcrumb"]'))).toBe(
      'Metro League › Season 2',
    );
  });

  describe('not found (I1)', () => {
    function expectNotFound(): void {
      expect(text(layout().querySelector('h1'))).toBe('Season not found');
      expect(linkIn(layout(), '← Back to League')?.getAttribute('href')).toBe('/leagues/1');
      expect(harness.fixture.debugElement.query(By.directive(SeasonStandings))).toBeNull();
      expect(layout().querySelector('[role="alert"]')).toBeNull();
    }

    it('shows "Season not found" for a 404, and no screen', async () => {
      await navigate();
      http.expectOne(`${BASE}/seasons/11/`).flush(...failure(404, 'Not found.'));
      await harness.fixture.whenStable();
      expectNotFound();
    });

    it('shows "Season not found" for another League\'s Season, and no screen', async () => {
      await navigate();
      await releaseSeasonLayout(harness, season({ league: { id: 2, name: 'Rival' } }));
      expectNotFound();
    });
  });

  describe('load error (I2)', () => {
    it('shows the message with Retry, which re-sends only the Season GET', async () => {
      await navigate();
      http.expectOne(`${BASE}/seasons/11/`).flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = layout().querySelector('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(text(layout().querySelector('h1'))).not.toBe('Season not found');
      expect(harness.fixture.debugElement.query(By.directive(SeasonStandings))).toBeNull();

      buttonIn(alert, 'Retry')?.click();
      await harness.fixture.whenStable();
      expect(http.match((req) => req.url.endsWith('/nav/'))).toHaveLength(0);
      await releaseSeasonLayout(harness);
      expect(layout().querySelector('[role="alert"]')).toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(SeasonStandings))).not.toBeNull();
    });
  });

  describe('breadcrumb', () => {
    it('links the League name to the League and the Season name to its dashboard', async () => {
      await navigate('standings');
      await releaseSeasonLayout(harness);
      const crumb = layout().querySelector('nav[aria-label="Breadcrumb"]');
      expect(text(crumb)).toBe('Metro League › Season 1');
      expect(linkIn(crumb, 'Metro League')?.getAttribute('href')).toBe('/leagues/1');
      expect(linkIn(crumb, 'Season 1')?.getAttribute('href')).toBe('/leagues/1/seasons/11');
    });
  });
});
