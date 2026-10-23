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
import { Match } from '../../api/matches.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { matchRow } from '../testing/match-fixtures';
import { releaseMatchesLoads } from '../testing/matches-loads';

const BASE = environment.apiBaseUrl;

/** 14:30 on Oct 5, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const PLAYED = new Date(2026, 9, 5, 14, 30).toISOString();

describe('Match detail (/matches/:id)', () => {
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
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;

  function matchRequest(id = 7): TestRequest {
    return http.expectOne(`${BASE}/matches/${id}/`);
  }

  async function open(match: Match): Promise<void> {
    await harness.navigateByUrl(`/matches/${match.id}`);
    matchRequest(match.id).flush({ data: match, meta: {} });
    await harness.fixture.whenStable();
  }

  it('gets the route id Match', async () => {
    await harness.navigateByUrl('/matches/12');
    const req = matchRequest(12);
    expect(req.request.method).toBe('GET');
    req.flush({ data: matchRow({ id: 12 }), meta: {} });
    await harness.fixture.whenStable();
  });

  it('shows Red vs Blue, each name in its side colour', async () => {
    await open(matchRow());
    expect(text(page().querySelector('[data-lf-side="red"]'))).toContain('Alpha');
    expect(text(page().querySelector('[data-lf-side="blue"]'))).toContain('Bravo');
    expect(text(page().querySelector('h1'))).toMatch(/Alpha.*vs.*Bravo/);
  });

  /** The Match Summary row whose header cell reads `label` ("Round 1", "Round 2", "Total"). */
  function summaryRow(label: string): HTMLTableRowElement | undefined {
    return [...page().querySelectorAll<HTMLTableRowElement>('.summary-table tbody tr')].find(
      (tr) => text(tr.querySelector('th')) === label,
    );
  }
  const cells = (tr: HTMLTableRowElement | undefined): string[] =>
    [...(tr?.querySelectorAll('td') ?? [])].map((td) => text(td));

  it('heads the team columns by name, each in its Round 1 colour', async () => {
    await open(matchRow());
    const heads = [...page().querySelectorAll('.summary-table thead th')];
    expect(heads.map((th) => text(th))).toEqual([
      'Round',
      'Alpha (Red in Round 1)',
      'Bravo (Blue in Round 1)',
      'Winner',
      'Details',
    ]);
    expect(heads[1].getAttribute('data-lf-side')).toBe('red');
    expect(heads[2].getAttribute('data-lf-side')).toBe('blue');
  });

  it("shows each Round's points per team and the Round's winner", async () => {
    await open(
      matchRow({
        red_round1_points: 4000,
        blue_round1_points: 3000,
        red_round2_points: 3500,
        blue_round2_points: 3600,
      }),
    );
    expect(cells(summaryRow('Round 1')).slice(0, 3)).toEqual(['4000', '3000', 'Alpha']);
    expect(cells(summaryRow('Round 2')).slice(0, 3)).toEqual(['3500', '3600', 'Bravo']);
  });

  it('reads "Tie" for a Round with level points', async () => {
    await open(matchRow({ red_round2_points: 3500, blue_round2_points: 3500 }));
    expect(cells(summaryRow('Round 2'))[2]).toBe('Tie');
  });

  it('marks a muted "+ elim bonus" on the team that eliminated the other that Round', async () => {
    // Round 1: Bravo (blue) was eliminated, so Alpha earned it. Round 2: Alpha was eliminated.
    await open(
      matchRow({
        red_round1_points: 4000,
        blue_round1_points: 3000,
        blue_round1_eliminated: true,
        red_round2_points: 3500,
        blue_round2_points: 3600,
        red_round2_eliminated: true,
      }),
    );
    const round1 = summaryRow('Round 1')?.querySelectorAll('td');
    const round2 = summaryRow('Round 2')?.querySelectorAll('td');
    expect(text(round1?.[0])).toBe('4000 + elim bonus');
    expect(text(round1?.[1])).toBe('3000');
    expect(text(round2?.[0])).toBe('3500');
    expect(text(round2?.[1])).toBe('3600 + elim bonus');
    expect(round1?.[0].querySelector('.match-detail__bonus')?.textContent).toContain('elim bonus');
  });

  it('links "View Round" to each played Round, round_ids[0] being Round 1', async () => {
    await open(matchRow({ round_ids: [21, 22] }));
    const link1 = summaryRow('Round 1')?.querySelector('a');
    const link2 = summaryRow('Round 2')?.querySelector('a');
    expect(text(link1)).toBe('View Round');
    expect(link1?.getAttribute('href')).toBe('/rounds/21');
    expect(text(link2)).toBe('View Round');
    expect(link2?.getAttribute('href')).toBe('/rounds/22');
  });

  it('reads "No round data", with no link, for a Round with no stored Round', async () => {
    await open(matchRow({ round_ids: [] }));
    for (const label of ['Round 1', 'Round 2']) {
      expect(cells(summaryRow(label))[3]).toBe('No round data');
      expect(summaryRow(label)?.querySelector('a')).toBeNull();
    }
  });

  it('reads "Not played yet", with no link, for Round 2 of an in-progress Match', async () => {
    await open(
      matchRow({
        is_completed: false,
        round_ids: [21],
        red_round2_points: 0,
        blue_round2_points: 0,
        winner: null,
        winner_name: null,
      }),
    );
    const round2 = summaryRow('Round 2');
    expect(cells(round2)).toEqual(['Not played yet']);
    expect(round2?.querySelector('a')).toBeNull();
    expect(text(round2)).not.toContain('Tie');
    expect(summaryRow('Round 1')?.querySelector('a')?.getAttribute('href')).toBe('/rounds/21');
    expect(text(page().querySelector('.result-chip'))).toBe('In progress');
  });

  it("totals each team's points with the Match winner", async () => {
    await open(
      matchRow({ red_total_points: 17250, blue_total_points: 9100, winner_name: 'Alpha' }),
    );
    expect(cells(summaryRow('Total')).slice(0, 3)).toEqual(['17250', '9100', 'Alpha']);
  });

  it('reads "Tie" in the Total row for a completed Match with no winner', async () => {
    await open(matchRow({ winner: null, winner_name: null }));
    expect(cells(summaryRow('Total'))[2]).toBe('Tie');
  });

  const note = (): string => text(page().querySelector('.match-detail__note'));

  it('notes that teams swap colours for Round 2', async () => {
    await open(matchRow({ red_rounds_won: 2, blue_rounds_won: 0 }));
    expect(note()).toBe('Teams swap colours for Round 2.');
  });

  it('explains the tiebreak when the Rounds are split and a team still won', async () => {
    await open(
      matchRow({ red_rounds_won: 1, blue_rounds_won: 1, winner: 3, winner_name: 'Alpha' }),
    );
    expect(note()).toBe(
      'Teams swap colours for Round 2. Rounds split 1–1, so total points decided the Match.',
    );
  });

  it('has no tiebreak line for a split Match with no winner', async () => {
    await open(
      matchRow({ red_rounds_won: 1, blue_rounds_won: 1, winner: null, winner_name: null }),
    );
    expect(note()).toBe('Teams swap colours for Round 2.');
  });

  it('heads the page with the Match type and the date played', async () => {
    await open(matchRow({ match_type: 'tournament', date_played: PLAYED }));
    expect(text(page().querySelector('.match-detail__meta'))).toMatch(
      /^Tournament Match · Oct 5, 2026 14:30 ·/,
    );
  });

  it('keeps the title "Match - Laserforce Simulator"', async () => {
    await open(matchRow());
    expect(document.title).toBe('Match - Laserforce Simulator');
  });

  it('shows the total score red - blue and the date played', async () => {
    await open(matchRow({ red_total_points: 17250, blue_total_points: 9100, date_played: PLAYED }));
    expect(text(page())).toContain('17250 - 9100');
    expect(text(page())).toContain('Oct 5, 2026 14:30');
  });

  it("shows the winner's name as the result", async () => {
    await open(matchRow({ winner: 4, winner_name: 'Bravo' }));
    expect(text(page().querySelector('.result-chip'))).toBe('Bravo');
  });

  it('shows "Tie" for a completed Match with no winner', async () => {
    await open(matchRow({ winner: null, winner_name: null }));
    expect(text(page().querySelector('.result-chip'))).toBe('Tie');
  });

  it('shows a muted "In progress", not "Tie", for an unfinished Match', async () => {
    await open(matchRow({ is_completed: false, winner: null, winner_name: null }));
    const chip = page().querySelector('.result-chip');
    expect(text(chip)).toBe('In progress');
    expect(chip?.classList).toContain('result-chip--muted');
    expect(text(page())).not.toContain('Tie');
  });

  it('links Back to Matches', async () => {
    await open(matchRow());
    const back = [...page().querySelectorAll('a')].find((a) => text(a).includes('Back to Matches'));
    expect(back?.getAttribute('href')).toBe('/matches');
    back?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/matches');
    releaseMatchesLoads();
  });

  it('shows "Match not found" with the back link on a 404', async () => {
    await harness.navigateByUrl('/matches/99');
    matchRequest(99).flush(
      { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
      { status: 404, statusText: 'Not Found' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('Match not found');
    const back = [...page().querySelectorAll('a')].find((a) => text(a).includes('Back to Matches'));
    expect(back?.getAttribute('href')).toBe('/matches');
    expect(page().querySelector('.result-chip')).toBeNull();
  });

  it('shows an alert for any other load failure', async () => {
    await harness.navigateByUrl('/matches/7');
    matchRequest(7).flush(
      { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toContain('Could not load the Match.');
    expect(text(page())).not.toContain('Match not found');
  });
});
