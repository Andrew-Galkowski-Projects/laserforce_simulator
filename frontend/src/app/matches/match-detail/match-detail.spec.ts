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

describe('Match stub (/matches/:id)', () => {
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
