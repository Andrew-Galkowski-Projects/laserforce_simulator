import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { NewTeamCandidates } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { releaseLeaguesLoads } from '../testing/leagues-loads';
import { leagueNav, newTeamCandidates, season } from '../testing/league-fixtures';

const BASE = environment.apiBaseUrl;
const CANDIDATES = `${BASE}/leagues/1/new-team-candidates/`;
const REASSIGN = `${BASE}/leagues/1/reassign-team/`;

describe('New-team picker (/leagues/:id/new-team, spec #409 F12)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const screen = (): HTMLElement => page().querySelector<HTMLElement>('app-league-new-team')!;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;

  function fail(status: number, code: string, message: string) {
    return [
      { errors: [{ code, message, field: null }], meta: {} },
      { status, statusText: 'Error' },
    ] as const;
  }

  async function navigate(): Promise<TestRequest> {
    await harness.navigateByUrl('/leagues/1/new-team');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    return http.expectOne(CANDIDATES);
  }

  async function open(body: NewTeamCandidates = newTeamCandidates()): Promise<void> {
    (await navigate()).flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  const radios = (): HTMLInputElement[] => [
    ...screen().querySelectorAll<HTMLInputElement>('input[type="radio"]'),
  ];
  const submit = (): HTMLButtonElement =>
    screen().querySelector<HTMLButtonElement>('#new-team-submit')!;

  it('loads the candidates with GET leagues/{id}/new-team-candidates/', async () => {
    const req = await navigate();
    expect(req.request.method).toBe('GET');
    req.flush({ data: newTeamCandidates(), meta: {} });
    await harness.fixture.whenStable();
  });

  it('shows the new_team.html heading and text', async () => {
    await open();
    expect(text(screen().querySelector('h1'))).toBe("You've been fired — choose a new team");
    expect(text(screen().querySelector('p'))).toBe(
      'The Owner has let you go. You may take over one of the 2 worst teams from Season 1 ' +
        '(your old team is not on the list).',
    );
  });

  it('lists each candidate as a radio row (rank, team, W-L-T), the first checked', async () => {
    await open();
    const headers = [...screen().querySelectorAll('th')].map((th) => text(th));
    expect(headers).toEqual(['', 'Rank', 'Team', 'W-L-T']);
    const rows = [...screen().querySelectorAll('tbody tr')];
    expect(rows.map((r) => r.id)).toEqual(['new-team-option-7', 'new-team-option-8']);
    expect(rows.map((r) => [...r.querySelectorAll('td')].slice(1).map((td) => text(td)))).toEqual([
      ['8', 'Green Giants', '1-6-0'],
      ['7', 'Gold', '2-5-0'],
    ]);
    expect(radios().map((r) => [r.value, r.checked])).toEqual([
      ['7', true],
      ['8', false],
    ]);
  });

  it('"Take Over This Team" POSTs the chosen team_id and opens the dashboard on 201', async () => {
    await open();
    radios()[1].click();
    await harness.fixture.whenStable();
    expect(text(submit())).toBe('Take Over This Team');
    submit().click();
    await harness.fixture.whenStable();
    const req = http.expectOne(REASSIGN);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ team_id: 8 });
    req.flush({ data: season(), meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    releaseLeaguesLoads();
    expect(routerUrl()).toBe('/leagues/1');
  });

  it('submits the first row when nothing else is picked', async () => {
    await open();
    submit().click();
    await harness.fixture.whenStable();
    const req = http.expectOne(REASSIGN);
    expect(req.request.body).toEqual({ team_id: 7 });
    req.flush({ data: season(), meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    releaseLeaguesLoads();
  });

  it('disables the button while the request runs (I3)', async () => {
    await open();
    submit().click();
    await harness.fixture.whenStable();
    expect(submit().disabled).toBe(true);
    http
      .expectOne(REASSIGN)
      .flush(...fail(409, 'manager_not_fired', 'The manager has not been fired.'));
    await harness.fixture.whenStable();
    expect(submit().disabled).toBe(false);
  });

  it.each([
    ['manager_not_fired', 'The manager has not been fired.'],
    ['no_completed_season', 'This League has no completed Season.'],
    ['not_career_league', 'Only a career League has a manager.'],
  ])(
    'shows a 409 %s message with a link back to the dashboard, keeping the table (I3)',
    async (code, message) => {
      await open();
      submit().click();
      await harness.fixture.whenStable();
      http.expectOne(REASSIGN).flush(...fail(409, code, message));
      await harness.fixture.whenStable();
      const alert = screen().querySelector('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe(message);
      expect(alert?.querySelector('a')?.getAttribute('href')).toBe('/leagues/1');
      expect(radios()).toHaveLength(2);
      expect(routerUrl()).toBe('/leagues/1/new-team');
    },
  );

  it('shows a 400 message from the API (I3)', async () => {
    await open();
    submit().click();
    await harness.fixture.whenStable();
    http.expectOne(REASSIGN).flush(...fail(400, 'invalid', 'Pick one of the listed teams.'));
    await harness.fixture.whenStable();
    expect(text(screen().querySelector('[role="alert"] span'))).toBe(
      'Pick one of the listed teams.',
    );
  });

  describe('load errors (I2)', () => {
    it('shows not-found for a 404', async () => {
      (await navigate()).flush(...fail(404, 'not_found', 'Not found.'));
      await harness.fixture.whenStable();
      expect(text(screen().querySelector('h1'))).toBe('League not found');
      expect(radios()).toHaveLength(0);
      expect(screen().querySelector('[role="alert"]')).toBeNull();
    });

    it('shows a 409 load in the error box with a dashboard link, and Retry loads again', async () => {
      (await navigate()).flush(
        ...fail(409, 'manager_not_fired', 'The manager has not been fired.'),
      );
      await harness.fixture.whenStable();
      const alert = screen().querySelector('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('The manager has not been fired.');
      expect(alert?.querySelector('a')?.getAttribute('href')).toBe('/leagues/1');
      expect(radios()).toHaveLength(0);

      [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry')?.click();
      await harness.fixture.whenStable();
      http.expectOne(CANDIDATES).flush({ data: newTeamCandidates(), meta: {} });
      await harness.fixture.whenStable();
      expect(screen().querySelector('[role="alert"]')).toBeNull();
      expect(radios()).toHaveLength(2);
    });
  });
});
