import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { GameRound } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { roundDetail } from '../testing/match-fixtures';

const BASE = environment.apiBaseUrl;

/** 09:05 on Mar 17, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const PLAYED = new Date(2026, 2, 17, 9, 5).toISOString();

describe('Single Round stub (/rounds/:id)', () => {
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
  const backLink = (): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes('Back to Matches'));

  function roundRequest(id = 9): TestRequest {
    return http.expectOne(`${BASE}/rounds/${id}/`);
  }

  async function open(round: GameRound): Promise<void> {
    await harness.navigateByUrl(`/rounds/${round.id}`);
    roundRequest(round.id).flush({ data: round, meta: {} });
    await harness.fixture.whenStable();
  }

  it('gets the route id Round, once', async () => {
    await harness.navigateByUrl('/rounds/31');
    const req = roundRequest(31);
    expect(req.request.method).toBe('GET');
    req.flush({ data: roundDetail({ id: 31 }), meta: {} });
    await harness.fixture.whenStable();
  });

  it('shows Red vs Blue in side colours, the score red - blue and the date', async () => {
    await open(
      roundDetail({
        team_red_name: 'Comets',
        team_blue_name: 'Vipers',
        red_points: 5120,
        blue_points: 4880,
        date_played: PLAYED,
      }),
    );
    expect(text(page().querySelector('[data-lf-side="red"]'))).toContain('Comets');
    expect(text(page().querySelector('[data-lf-side="blue"]'))).toContain('Vipers');
    expect(text(page())).toContain('5120 - 4880');
    expect(text(page())).toContain('Mar 17, 2026 09:05');
    expect(backLink()?.getAttribute('href')).toBe('/matches');
  });

  it("shows the winner's name as the result", async () => {
    await open(roundDetail({ winner: 4, winner_name: 'Bravo' }));
    expect(text(page().querySelector('.result-chip'))).toBe('Bravo');
  });

  it('shows "Tie" when the Round has no winner', async () => {
    await open(roundDetail({ winner: null, winner_name: null }));
    expect(text(page().querySelector('.result-chip'))).toBe('Tie');
  });

  it('shows "Single Round not found" with the back link on a 404', async () => {
    await harness.navigateByUrl('/rounds/99');
    roundRequest(99).flush(
      { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
      { status: 404, statusText: 'Not Found' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('Single Round not found');
    expect(backLink()?.getAttribute('href')).toBe('/matches');
    expect(page().querySelector('.result-chip')).toBeNull();
  });
});
