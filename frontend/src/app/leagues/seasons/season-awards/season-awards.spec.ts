import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { SeasonAwards } from '../../../api/seasons.models';
import { SELF_INSERT, USER, signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { User } from '../../../auth/user';
import { awardWinner } from '../../testing/league-fixtures';
import { failure } from '../../testing/league-stats-fixtures';
import { seasonAwards, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';
import { releasePlayControls } from '../../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

describe('Season Awards (/leagues/:id/seasons/:seasonId/awards, spec #444)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(user: User = USER): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn(user);
    harness = await RouterTestingHarness.create();
  }

  afterEach(() => {
    http.match((req) => req.url.endsWith('/nav/'));
    releasePlayControls();
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector('app-season-awards') as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const cells = (row: Element): string[] => [...row.children].map(text);
  const bodyRows = (): string[][] => [...page().querySelectorAll('table tbody tr')].map(cells);

  function awardsRequest(): TestRequest {
    const req = http.expectOne(`${BASE}/seasons/11/awards/`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  /** Lands on Season 11's awards and answers its GET with `body`. */
  async function open(body: SeasonAwards = seasonAwards(), user: User = USER): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(seasonUrl('awards'));
    await releaseSeasonLayout(harness);
    awardsRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  it('heads the page with the breadcrumb, h1 "Awards" and a "Standings" link', async () => {
    await open();
    const crumbs = [...(q('nav[aria-label="Breadcrumb"]')?.querySelectorAll('a') ?? [])];
    expect(crumbs.map((a) => [text(a), a.getAttribute('href')])).toEqual([
      ['Metro League', '/leagues/1'],
      ['Season 1', '/leagues/1/seasons/11'],
    ]);
    expect(text(q('h1'))).toBe('Awards');
    const standings = [...page().querySelectorAll('a')].find((a) => text(a) === 'Standings');
    expect(standings?.getAttribute('href')).toBe('/leagues/1/seasons/11/standings');
  });

  it('loads GET seasons/{id}/awards/ into a table "Award", "Winner", "Team", "Role", "Value"', async () => {
    await open();
    expect([...page().querySelectorAll('table thead th')].map(text)).toEqual([
      'Award',
      'Winner',
      'Team',
      'Role',
      'Value',
    ]);
  });

  it('lists the 11 awards in the template order, each value at its decimals', async () => {
    const w = (player_name: string, role: string, value: number) =>
      awardWinner({ player_name, role, value });
    await open(
      seasonAwards({
        most_points: w('Ace', 'commander', 1234.4),
        best_accuracy: w('Bo', 'scout', 61.26),
        kd_by_role: {
          commander: w('Cy', 'commander', 2.5),
          heavy: w('Di', 'heavy', 1.234),
          scout: w('Ed', 'scout', 3),
          medic: w('Fi', 'medic', 0.456),
          ammo: w('Gus', 'ammo', 1.1),
        },
        best_medic: w('Hal', 'medic', 812.6),
        most_efficient_nuke: w('Ivy', 'commander', 2.345678),
        season_mvp: w('Jo', 'heavy', 88.44),
        finals_mvp: w('Kit', 'scout', 12),
      }),
    );
    expect(bodyRows()).toEqual([
      ['Most Points', 'Ace', 'Red Raiders', 'commander', '1234'],
      ['Best Accuracy', 'Bo', 'Red Raiders', 'scout', '61.3'],
      ['K/D — Commander', 'Cy', 'Red Raiders', 'commander', '2.50'],
      ['K/D — Heavy', 'Di', 'Red Raiders', 'heavy', '1.23'],
      ['K/D — Scout', 'Ed', 'Red Raiders', 'scout', '3.00'],
      ['K/D — Medic', 'Fi', 'Red Raiders', 'medic', '0.46'],
      ['K/D — Ammo', 'Gus', 'Red Raiders', 'ammo', '1.10'],
      ['Best Medic', 'Hal', 'Red Raiders', 'medic', '813'],
      ['Most Efficient Nuke', 'Ivy', 'Red Raiders', 'commander', '2.35'],
      ['Season MVP', 'Jo', 'Red Raiders', 'heavy', '88.4'],
      ['Finals MVP', 'Kit', 'Red Raiders', 'scout', '12.0'],
    ]);
  });

  it('shows "—" in all four cells of an award nobody won (I6)', async () => {
    await open(seasonAwards({ most_points: null, kd_by_role: { ...seasonAwards().kd_by_role } }));
    const rows = bodyRows();
    expect(rows[0]).toEqual(['Most Points', '—', '—', '—', '—']);
    expect(rows[3]).toEqual(['K/D — Heavy', '—', '—', '—', '—']);
    expect(rows[2]).toEqual(['K/D — Commander', 'Ace', 'Red Raiders', 'commander', '2.50']);
  });

  it('explains an unplayed Season with no table when no rounds are played (I6)', async () => {
    await open(seasonAwards({ has_rounds: false }));
    expect(text(q('[role="status"]'))).toBe(
      'No completed regular-season rounds yet — awards appear once the Season has been played.',
    );
    expect(q('table')).toBeNull();
  });

  describe('"You" (I4)', () => {
    const awards = seasonAwards({
      most_points: awardWinner({ player_id: 9, player_name: 'Dan' }),
      best_accuracy: awardWinner({ player_id: 25, player_name: 'Ace' }),
    });
    const marked = (): string[] =>
      [...page().querySelectorAll('table tbody tr')]
        .filter((r) => r.querySelector('.you'))
        .map((r) => text(r.firstElementChild));

    it("marks the signed-in Account's Self-insert Player's awards", async () => {
      await open(awards, { ...USER, self_insert: SELF_INSERT });
      expect(marked()).toEqual(['Most Points']);
      expect(bodyRows()[0][1]).toBe('Dan You');
    });

    it('marks nobody without a Self-insert Player', async () => {
      await open(awards, { ...USER, self_insert: null });
      expect(marked()).toEqual([]);
    });
  });

  describe('load (I2)', () => {
    it('shows a failed load with Retry, which re-sends only the awards GET', async () => {
      await setUp();
      await harness.navigateByUrl(seasonUrl('awards'));
      await releaseSeasonLayout(harness);
      awardsRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('table')).toBeNull();

      const retry = [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry');
      retry?.click();
      await harness.fixture.whenStable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      awardsRequest().flush({ data: seasonAwards(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(bodyRows()).toHaveLength(11);
    });
  });
});
