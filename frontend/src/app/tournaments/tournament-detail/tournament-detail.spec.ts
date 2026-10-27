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
import { signIn } from '../../api/testing/auth-fixtures';
import { TournamentDetail as TournamentDetailData } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { tournamentDetail } from '../testing/detail-fixtures';
import { releasePoolLoads } from '../testing/pool-fixtures';

const BASE = environment.apiBaseUrl;

describe('Tournament detail (/tournaments/:id)', () => {
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

  function detailRequest(id = 5): TestRequest {
    return http.expectOne(`${BASE}/tournaments/${id}/`);
  }

  async function open(detail: TournamentDetailData): Promise<void> {
    await harness.navigateByUrl(`/tournaments/${detail.id}`);
    detailRequest(detail.id).flush({ data: detail, meta: {} });
    await harness.fixture.whenStable();
  }

  const header = (): HTMLElement | null => page().querySelector('.tournament-detail__header');

  it('gets the route id Tournament', async () => {
    await harness.navigateByUrl('/tournaments/9');
    const req = detailRequest(9);
    expect(req.request.method).toBe('GET');
    req.flush({ data: tournamentDetail({ id: 9 }), meta: {} });
    await harness.fixture.whenStable();
  });

  it('shows the name, format label and State badge in the header', async () => {
    await open(
      tournamentDetail({
        name: 'Winter Open',
        format: 'round_robin_double_elim',
        format_label: 'Random Draw',
        state: 'active',
      }),
    );
    expect(text(page().querySelector('h1'))).toBe('Winter Open');
    expect(text(header())).toContain('Random Draw');
    const badge = header()?.querySelector('.tournament-state');
    expect(text(badge)).toBe('Active');
    expect(badge?.getAttribute('data-state')).toBe('active');
  });

  it.each([
    ['preset', 'Preset teams', 'Player pool'],
    ['player_pool', 'Player pool', 'Preset teams'],
  ] as const)('labels a %s assembly "%s"', async (assembly, label, other) => {
    await open(tournamentDetail({ team_assembly: assembly, participants: [] }));
    releasePoolLoads();
    expect(text(header())).toContain(label);
    expect(text(header())).not.toContain(other);
  });

  const champion = (): HTMLElement | null => page().querySelector('.tournament-detail__champion');

  it('shows "Champion: <team>" linking to the Team when a champion is set', async () => {
    await open(tournamentDetail({ state: 'completed', champion: { id: 12, name: 'Blue Bolts' } }));
    expect(text(champion())).toBe('Champion: Blue Bolts');
    expect(champion()?.querySelector('a')?.getAttribute('href')).toBe('/teams/12');
  });

  it('shows no champion line while none is set', async () => {
    await open(tournamentDetail());
    expect(champion()).toBeNull();
    expect(page().textContent).not.toContain('Champion');
  });

  const leagueNote = (): HTMLElement | null => page().querySelector('.tournament-detail__league');

  it('notes "In League" with the Season name for an embedded Tournament', async () => {
    await open(tournamentDetail({ embedded: true, season: { id: 3, name: 'Season 2026' } }));
    expect(text(leagueNote())).toBe('In League: Season 2026');
  });

  it('shows no In League note for a sandbox Tournament', async () => {
    await open(tournamentDetail());
    expect(leagueNote()).toBeNull();
    expect(page().textContent).not.toContain('In League');
  });

  it('shows the not-found state on a 404 (missing, or another Account)', async () => {
    await harness.navigateByUrl('/tournaments/404');
    detailRequest(404).flush(
      { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
      { status: 404, statusText: 'Not Found' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('Tournament not found');
    expect(page().querySelector('a[href="/tournaments"]')).not.toBeNull();
    expect(page().querySelector('[role="alert"]')).toBeNull();
  });

  it('shows any other load failure in an alert', async () => {
    await harness.navigateByUrl('/tournaments/5');
    detailRequest(5).flush(
      { errors: [{ code: 'server_error', message: 'Database is down.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toBe('Database is down.');
    expect(page().textContent).not.toContain('Tournament not found');
  });

  const participantRows = (): string[][] =>
    [...page().querySelectorAll('.tournament-participants tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => text(td)),
    );

  it('lists the participants by seed, each Team linking to its page', async () => {
    await open(
      tournamentDetail({
        participants: [
          { team: { id: 11, name: 'Red Raiders' }, seed: 1 },
          { team: { id: 12, name: 'Blue Bolts' }, seed: 2 },
          { team: { id: 13, name: 'Green Giants' }, seed: 3 },
        ],
      }),
    );
    const table = page().querySelector('.tournament-participants');
    expect([...(table?.querySelectorAll('th') ?? [])].map((th) => text(th))).toEqual([
      'Seed',
      'Team',
    ]);
    expect(participantRows()).toEqual([
      ['1', 'Red Raiders'],
      ['2', 'Blue Bolts'],
      ['3', 'Green Giants'],
    ]);
    expect(
      [...(table?.querySelectorAll('tbody a') ?? [])].map((a) => a.getAttribute('href')),
    ).toEqual(['/teams/11', '/teams/12', '/teams/13']);
  });

  it('has no Participants table without participants', async () => {
    await open(tournamentDetail({ participants: [] }));
    expect(page().querySelector('.tournament-participants')).toBeNull();
  });

  it('has no Participants table for a Player-pool Tournament, even once drawn', async () => {
    await open(tournamentDetail({ team_assembly: 'player_pool', is_drawn: true }));
    releasePoolLoads();
    expect(page().querySelector('.tournament-participants')).toBeNull();
  });

  it('places the Player Pool section for a Player-pool Tournament only', async () => {
    await open(tournamentDetail({ team_assembly: 'player_pool', participants: [], pool_size: 6 }));
    releasePoolLoads();
    expect(text(page().querySelector('app-tournament-pool h2'))).toBe('Player Pool (6)');
  });

  it('has no Player Pool section for a preset Tournament', async () => {
    await open(tournamentDetail());
    expect(page().querySelector('app-tournament-pool')).toBeNull();
  });
});
