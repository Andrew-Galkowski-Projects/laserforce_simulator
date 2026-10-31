import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { OwnerEvaluation as Evaluation } from '../../../api/seasons.models';
import { USER, signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { failure } from '../../testing/league-stats-fixtures';
import { ownerEvaluation, season, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';

const BASE = environment.apiBaseUrl;

describe('Owner Evaluation (/leagues/:id/seasons/:seasonId/owner-evaluation, spec #444)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn(USER);
    harness = await RouterTestingHarness.create();
  }

  afterEach(() => {
    // Whatever a navigation away (a new Season, the Team picker) loads, and the League nav.
    http.match((req) => req.method === 'GET' && !req.url.endsWith('/owner-evaluation/'));
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector(
      'app-owner-evaluation',
    ) as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a) === label);
  const button = (label: string): HTMLButtonElement | undefined =>
    [...page().querySelectorAll('button')].find((b) => text(b) === label);
  const cells = (row: Element | null): string[] => [...(row?.children ?? [])].map(text);
  const url = (): string => TestBed.inject(Router).url;

  function evaluationRequest(): TestRequest {
    const req = http.expectOne(`${BASE}/seasons/11/owner-evaluation/`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  function nextSeasonRequest(): TestRequest {
    const req = http.expectOne(`${BASE}/leagues/1/next-season/`);
    expect(req.request.method).toBe('POST');
    return req;
  }

  /** Lands on Season 11's evaluation (a completed Season) and answers its GET with `body`. */
  async function open(body: Evaluation = ownerEvaluation()): Promise<void> {
    await setUp();
    await harness.navigateByUrl(seasonUrl('owner-evaluation'));
    await releaseSeasonLayout(harness, season({ state: 'completed' }));
    evaluationRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  it('shows the breadcrumb above the h1 "Annual Performance Evaluation"', async () => {
    await open();
    expect(text(q('nav[aria-label="Breadcrumb"]'))).toBe('Metro League › Season 1');
    expect(text(q('h1'))).toBe('Annual Performance Evaluation');
  });

  it.each([
    ['retained', 'RETAINED'],
    ['hot_seat', 'HOT_SEAT'],
    ['fired', 'FIRED'],
  ] as const)('shows the %s verdict as a "%s" badge', async (verdict, label) => {
    await open(ownerEvaluation({ verdict, hot_seat_level: verdict === 'hot_seat' ? 1 : 0 }));
    expect(text(q('.owner-evaluation__verdict'))).toBe(`The Owner's verdict: ${label}`);
    const badge = q('.owner-evaluation__badge');
    expect(text(badge)).toBe(label);
    expect(badge?.dataset['verdict']).toBe(verdict);
  });

  describe('hot seat and firing texts', () => {
    it('says nothing more for a retained Manager', async () => {
      await open();
      expect(q('.owner-evaluation__hot-seat')).toBeNull();
      expect(q('.owner-evaluation__fired')).toBeNull();
    });

    it.each([
      [1, "Another season like that and you're fired!"],
      [2, "A couple more seasons like that and you're fired!"],
    ])('warns a hot seat level %i Manager "%s"', async (level, warning) => {
      await open(ownerEvaluation({ verdict: 'hot_seat', hot_seat_level: level }));
      expect(text(q('.owner-evaluation__hot-seat'))).toBe(warning);
      expect(q('.owner-evaluation__fired')).toBeNull();
    });

    it('says a luxury-tax firing "Fired for paying the luxury tax."', async () => {
      await open(ownerEvaluation({ verdict: 'fired', fired_reason: 'luxury_tax', is_fired: true }));
      expect(text(q('.owner-evaluation__fired'))).toBe('Fired for paying the luxury tax.');
      expect(q('.owner-evaluation__hot-seat')).toBeNull();
    });

    it('says any other firing is the mood', async () => {
      await open(ownerEvaluation({ verdict: 'fired', fired_reason: 'mood', is_fired: true }));
      expect(text(q('.owner-evaluation__fired'))).toBe('Fired by the owner — mood fell too low.');
    });
  });

  describe('factor table', () => {
    it('lists Wins and Playoffs to 3 places and Money to 1, with the overall mood', async () => {
      await open(
        ownerEvaluation({
          wins_delta: 0.125,
          wins_total: 0.6254,
          playoffs_delta: -0.05,
          playoffs_total: 0.3,
          money_delta: -1.26,
          money_total: 4.5,
          overall_mood: 5.4254,
        }),
      );
      const table = q('table');
      expect(cells(table?.querySelector('thead tr') ?? null)).toEqual([
        'Factor',
        'This Season',
        'Cumulative',
      ]);
      expect([...(table?.querySelectorAll('tbody tr') ?? [])].map(cells)).toEqual([
        ['Wins', '0.125', '0.625'],
        ['Playoffs', '-0.050', '0.300'],
        ['Money', '-1.3', '4.5'],
      ]);
      expect(cells(table?.querySelector('tfoot tr') ?? null)).toEqual([
        'Overall mood',
        '',
        '5.425',
      ]);
    });

    it('never prints a negative zero', async () => {
      await open(ownerEvaluation({ money_delta: -0.04, overall_mood: -0.0001 }));
      const money = [...page().querySelectorAll('tbody tr')].map(cells)[2];
      expect(money[1]).toBe('0.0');
      expect(cells(q('tfoot tr'))[2]).toBe('0.000');
    });
  });

  describe('call to action', () => {
    it('links "Choose New Team" to the Team picker for a fired, not reassigned Manager', async () => {
      await open(ownerEvaluation({ verdict: 'fired', is_fired: true, reassigned: false }));
      expect(link('Choose New Team')?.getAttribute('href')).toBe('/leagues/1/new-team');
      expect(button('Start Next Season')).toBeUndefined();
    });

    it.each([
      ['retained', ownerEvaluation()],
      [
        'fired and reassigned',
        ownerEvaluation({ verdict: 'fired', is_fired: true, reassigned: true }),
      ],
    ])('offers "Start Next Season" to a %s Manager', async (_, body) => {
      await open(body);
      expect(button('Start Next Season')).toBeDefined();
      expect(link('Choose New Team')).toBeUndefined();
    });

    it('starts the next Season and goes to its dashboard', async () => {
      await open();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      const req = nextSeasonRequest();
      expect(req.request.body).toEqual({});
      req.flush({ data: season({ id: 12, name: 'Season 2', state: 'active' }), meta: {} });
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/seasons/12');
    });

    it('locks the button while the request is in flight (I3)', async () => {
      await open();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      expect(button('Start Next Season')?.disabled).toBe(true);
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      nextSeasonRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      expect(button('Start Next Season')?.disabled).toBe(false);
    });

    it('sends a fired Manager (409 manager_fired) to the Team picker', async () => {
      await open();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      nextSeasonRequest().flush(
        {
          errors: [{ code: 'manager_fired', message: 'You were fired.', field: null }],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/new-team');
    });

    it('shows any other failure, keeping the evaluation on screen (I3)', async () => {
      await open();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      nextSeasonRequest().flush(...failure(409, 'Season 1 is not completed.'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/seasons/11/owner-evaluation');
      expect(text(q('.owner-evaluation__write-error'))).toBe('Season 1 is not completed.');
      expect(q('.owner-evaluation__write-error')?.getAttribute('role')).toBe('alert');
      expect(text(q('.owner-evaluation__badge'))).toBe('RETAINED');
      expect(q('table')).not.toBeNull();
      expect(button('Start Next Season')?.disabled).toBe(false);
    });

    it('clears the last failure when the next attempt starts', async () => {
      await open();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      nextSeasonRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      button('Start Next Season')?.click();
      await harness.fixture.whenStable();
      expect(q('.owner-evaluation__write-error')).toBeNull();
      nextSeasonRequest().flush(...failure(500, 'Still down.'));
      await harness.fixture.whenStable();
    });
  });

  describe('load', () => {
    it('shows "Owner evaluation not found" with a link back to the Season on a 404', async () => {
      await setUp();
      await harness.navigateByUrl(seasonUrl('owner-evaluation'));
      await releaseSeasonLayout(harness);
      evaluationRequest().flush(...failure(404, 'No owner evaluation for this Season.'));
      await harness.fixture.whenStable();
      expect(text(q('h1'))).toBe('Owner evaluation not found');
      expect(link('← Back to Season')?.getAttribute('href')).toBe('/leagues/1/seasons/11');
      expect(q('table')).toBeNull();
      expect(button('Start Next Season')).toBeUndefined();
    });

    it('shows a failed load with Retry, which re-sends only the evaluation GET (I2)', async () => {
      await setUp();
      await harness.navigateByUrl(seasonUrl('owner-evaluation'));
      await releaseSeasonLayout(harness);
      evaluationRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('table')).toBeNull();

      [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry')?.click();
      await harness.fixture.whenStable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      evaluationRequest().flush({ data: ownerEvaluation(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(text(q('.owner-evaluation__badge'))).toBe('RETAINED');
    });
  });
});
