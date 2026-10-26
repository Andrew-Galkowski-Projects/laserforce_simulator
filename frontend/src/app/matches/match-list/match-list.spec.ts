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
import { GameRoundListItem } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { matchRow, pageEnvelope, roundRow } from '../testing/match-fixtures';
import { releaseTeamsLoads } from '../../teams/testing/teams-loads';
import { releaseMatchesLoads } from '../testing/matches-loads';

const BASE = environment.apiBaseUrl;

/** 14:30 on Oct 5, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const PLAYED = new Date(2026, 9, 5, 14, 30).toISOString();

describe('Match History (/matches)', () => {
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

  function matchesRequest(): TestRequest {
    return http.expectOne((req) => req.url === `${BASE}/matches/`);
  }

  function roundsRequest(): TestRequest {
    return http.expectOne((req) => req.url === `${BASE}/rounds/`);
  }

  async function open(
    matches: Match[],
    rounds: GameRoundListItem[],
    matchPages = 1,
    roundPages = 1,
  ): Promise<void> {
    await harness.navigateByUrl('/matches');
    matchesRequest().flush(pageEnvelope(matches, 1, matchPages, count(matches, matchPages)));
    roundsRequest().flush(pageEnvelope(rounds, 1, roundPages, count(rounds, roundPages)));
    await harness.fixture.whenStable();
  }

  /** A row count consistent with `pages` pages of 20 (one more than fits on the pages before). */
  function count(items: unknown[], pages: number): number {
    return pages > 1 ? (pages - 1) * 20 + 1 : items.length;
  }

  it('asks for the first 20 Matches and the first 20 Single Rounds, server-ordered', async () => {
    await harness.navigateByUrl('/matches');
    const matches = matchesRequest();
    expect(matches.request.method).toBe('GET');
    expect(matches.request.params.keys().sort()).toEqual(['page_size']);
    expect(matches.request.params.get('page_size')).toBe('20');
    const rounds = roundsRequest();
    expect(rounds.request.method).toBe('GET');
    expect(rounds.request.params.keys().sort()).toEqual(['page_size', 'single_round']);
    expect(rounds.request.params.get('page_size')).toBe('20');
    expect(rounds.request.params.get('single_round')).toBe('true');
    matches.flush(pageEnvelope([]));
    rounds.flush(pageEnvelope([]));
    await harness.fixture.whenStable();
  });

  const section = (name: 'matches' | 'rounds'): HTMLElement | null =>
    page().querySelector<HTMLElement>(`[data-section="${name}"]`);
  const rows = (name: 'matches' | 'rounds'): HTMLElement[] => [
    ...(section(name)?.querySelectorAll<HTMLElement>('[role="row"]:not(.mat-mdc-header-row)') ??
      []),
  ];
  const cells = (row: HTMLElement | undefined): string[] =>
    [...(row?.querySelectorAll('[role="cell"]') ?? [])].map((c) => text(c));
  const headers = (name: 'matches' | 'rounds'): string[] =>
    [...(section(name)?.querySelectorAll('[role="columnheader"]') ?? [])].map((c) => text(c));

  it('shows each Match as Red vs Blue, Winner, Score, Rounds and Date played', async () => {
    await open(
      [
        matchRow({
          id: 7,
          team_red_name: 'Alpha',
          team_blue_name: 'Bravo',
          winner_name: 'Alpha',
          red_total_points: 17250,
          blue_total_points: 9100,
          red_rounds_won: 2,
          blue_rounds_won: 0,
          date_played: PLAYED,
        }),
      ],
      [],
    );
    expect(text(section('matches')?.querySelector('h2'))).toBe('Matches');
    expect(headers('matches')).toEqual(['Red vs Blue', 'Winner', 'Score', 'Rounds', 'Date']);
    expect(rows('matches')).toHaveLength(1);
    const [teams, ...rest] = cells(rows('matches')[0]);
    expect(teams).toMatch(/^R\s*Alpha\s*vs\s*B\s*Bravo$/);
    expect(rest).toEqual(['Alpha', '17250 - 9100', '2 - 0', 'Oct 5, 2026 14:30']);
    const row = rows('matches')[0];
    expect(text(row.querySelector('[data-lf-side="red"]'))).toContain('Alpha');
    expect(text(row.querySelector('[data-lf-side="blue"]'))).toContain('Bravo');
  });

  it('shows the Match winner as a chip, "Tie" with no winner, and a muted "In progress" when unfinished', async () => {
    await open(
      [
        matchRow({ id: 1, winner: 4, winner_name: 'Bravo' }),
        matchRow({ id: 2, winner: null, winner_name: null }),
        matchRow({ id: 3, is_completed: false, winner: null, winner_name: null }),
      ],
      [],
    );
    const chips = rows('matches').map((r) => r.querySelector('.result-chip'));
    expect(chips.map((c) => text(c))).toEqual(['Bravo', 'Tie', 'In progress']);
    expect(chips[0]?.classList).not.toContain('result-chip--muted');
    expect(chips[1]?.classList).not.toContain('result-chip--muted');
    expect(chips[2]?.classList).toContain('result-chip--muted');
  });

  it('shows each Single Round as Red vs Blue, Winner (or "Tie"), Score and Date played', async () => {
    await open(
      [],
      [
        roundRow({
          id: 9,
          team_red_name: 'Charlie',
          team_blue_name: 'Delta',
          winner_name: 'Delta',
          red_points: 3100,
          blue_points: 4200,
          date_played: PLAYED,
        }),
        roundRow({ id: 10, winner: null, winner_name: null }),
      ],
    );
    expect(text(section('rounds')?.querySelector('h2'))).toBe('Single Rounds');
    expect(headers('rounds')).toEqual(['Red vs Blue', 'Winner', 'Score', 'Date']);
    expect(rows('rounds')).toHaveLength(2);
    const [teams, ...rest] = cells(rows('rounds')[0]);
    expect(teams).toMatch(/^R\s*Charlie\s*vs\s*B\s*Delta$/);
    expect(rest).toEqual(['Delta', '3100 - 4200', 'Oct 5, 2026 14:30']);
    expect(text(rows('rounds')[0].querySelector('[data-lf-side="red"]'))).toContain('Charlie');
    expect(text(rows('rounds')[0].querySelector('[data-lf-side="blue"]'))).toContain('Delta');
    expect(text(rows('rounds')[1].querySelector('.result-chip'))).toBe('Tie');
  });

  /** Each row's anchors as [accessible text, href]. */
  const rowLinks = (row: HTMLElement): [string, string | null][] =>
    [...row.querySelectorAll('a')].map((a) => [
      a.getAttribute('aria-label') ?? text(a),
      a.getAttribute('href'),
    ]);

  it('keeps rows as table rows, each holding one link named by its matchup', async () => {
    await open(
      [
        matchRow({ id: 7, team_red_name: 'Crimson Wraiths', team_blue_name: 'Tempest Titans' }),
        matchRow({ id: 8, team_red_name: 'Alpha', team_blue_name: 'Bravo' }),
      ],
      [roundRow({ id: 9, team_red_name: 'Charlie', team_blue_name: 'Delta' })],
    );
    const matchRows = rows('matches');
    // A row is announced as a row (not a link); the link lives inside it.
    expect(matchRows.map((r) => r.getAttribute('role'))).toEqual(['row', 'row']);
    expect(matchRows.map((r) => r.tagName)).not.toContain('A');
    expect(matchRows.map(rowLinks)).toEqual([
      [['Crimson Wraiths vs Tempest Titans', '/matches/7']],
      [['Alpha vs Bravo', '/matches/8']],
    ]);
    const roundRows = rows('rounds');
    expect(roundRows.map((r) => r.getAttribute('role'))).toEqual(['row']);
    expect(roundRows.map(rowLinks)).toEqual([[['Charlie vs Delta', '/rounds/9']]]);
  });

  it.each([
    ['matches', '/matches/8'],
    ['rounds', '/rounds/9'],
  ] as const)('opens a %s row through its link at %s', async (name, url) => {
    await open([matchRow({ id: 8 })], [roundRow({ id: 9 })]);
    rows(name)[0].querySelector<HTMLAnchorElement>('a')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe(url);
    releaseMatchesLoads();
  });

  const links = (scope: Element | null | undefined): [string, string | null][] =>
    [...(scope?.querySelectorAll('a') ?? [])].map((a) => [text(a), a.getAttribute('href')]);

  it('has New Match (primary) and New Single Round (outlined) in the header', async () => {
    await open([matchRow()], [roundRow()]);
    const header = page().querySelector('.match-list__header');
    expect(text(header?.querySelector('h1'))).toBe('Match History');
    expect(links(header?.querySelector('.match-list__actions'))).toEqual([
      ['New Match', '/matches/new'],
      ['New Single Round', '/rounds/new'],
    ]);
    const [newMatch, newRound] = [...(header?.querySelectorAll('a') ?? [])];
    expect(newMatch.getAttribute('mat-flat-button')).not.toBeNull();
    expect(newRound.getAttribute('mat-stroked-button')).not.toBeNull();
  });

  it.each([
    ['New Match', '/matches/new'],
    ['New Single Round', '/rounds/new'],
  ])('opens %s at %s from the header', async (label, url) => {
    await open([matchRow()], [roundRow()]);
    const a = [...page().querySelectorAll<HTMLAnchorElement>('.match-list__header a')].find(
      (el) => text(el) === label,
    );
    a?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe(url);
    releaseMatchesLoads();
  });

  it('says "No matches played yet." when only the Matches section is empty', async () => {
    await open([], [roundRow()]);
    expect(rows('matches')).toHaveLength(0);
    expect(text(section('matches'))).toContain('No matches played yet.');
    expect(text(section('rounds'))).not.toContain('No single rounds played yet.');
    expect(page().querySelector('.match-list__empty')).toBeNull();
  });

  it('says "No single rounds played yet." when only the Single Rounds section is empty', async () => {
    await open([matchRow()], []);
    expect(rows('rounds')).toHaveLength(0);
    expect(text(section('rounds'))).toContain('No single rounds played yet.');
    expect(text(section('matches'))).not.toContain('No matches played yet.');
    expect(page().querySelector('.match-list__empty')).toBeNull();
  });

  it('shows one centred "No matches played yet" state with both buttons when nothing is played', async () => {
    await open([], []);
    const empty = page().querySelector('.match-list__empty');
    expect(text(empty?.querySelector('h2'))).toBe('No matches played yet');
    expect(links(empty)).toEqual([
      ['New Match', '/matches/new'],
      ['New Single Round', '/rounds/new'],
    ]);
    expect(section('matches')).toBeNull();
    expect(section('rounds')).toBeNull();
    expect(page().querySelector('.match-list__header .match-list__actions')).toBeNull();
  });

  const tools = (): Element | null =>
    page().querySelector('.match-list__header .match-list__tools');

  it.each([
    ['with games', [matchRow()]],
    ['with nothing played', []],
  ])('has Head-to-Head and Compare Rounds (outlined) in the header %s', async (_, matches) => {
    await open(matches, []);
    expect(links(tools())).toEqual([
      ['Head-to-Head', '/teams/head-to-head'],
      ['Compare Rounds', '/rounds/compare'],
    ]);
    for (const a of [...(tools()?.querySelectorAll('a') ?? [])]) {
      expect(a.getAttribute('mat-stroked-button')).not.toBeNull();
    }
  });

  it.each([
    ['Head-to-Head', '/teams/head-to-head'],
    ['Compare Rounds', '/rounds/compare'],
  ])('opens %s at %s from the header', async (label, url) => {
    await open([matchRow()], [roundRow()]);
    const a = [...(tools()?.querySelectorAll<HTMLAnchorElement>('a') ?? [])].find(
      (el) => text(el) === label,
    );
    a?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe(url);
    releaseMatchesLoads();
    releaseTeamsLoads();
  });

  const paginator = (name: 'matches' | 'rounds'): HTMLElement | null =>
    section(name)?.querySelector<HTMLElement>('mat-paginator') ?? null;

  async function next(name: 'matches' | 'rounds'): Promise<void> {
    paginator(name)?.querySelector<HTMLButtonElement>('button[aria-label="Next page"]')?.click();
    await harness.fixture.whenStable();
  }

  it('shows no paginator for a section that fits on one page', async () => {
    await open([matchRow()], [roundRow()], 1, 1);
    expect(paginator('matches')).toBeNull();
    expect(paginator('rounds')).toBeNull();
  });

  it('pages Matches alone: Next fetches Matches page 2 and leaves Single Rounds as they were', async () => {
    await open([matchRow({ id: 1, team_red_name: 'Page1' })], [roundRow({ id: 9 })], 2, 1);
    expect(paginator('matches')).not.toBeNull();
    expect(paginator('rounds')).toBeNull();

    await next('matches');
    const req = matchesRequest();
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('20');
    http.expectNone((r) => r.url === `${BASE}/rounds/`);
    req.flush(pageEnvelope([matchRow({ id: 2, team_red_name: 'Page2' })], 2, 2, 21));
    await harness.fixture.whenStable();

    expect(text(rows('matches')[0])).toContain('Page2');
    expect(rows('rounds').map((r) => r.querySelector('a')?.getAttribute('href'))).toEqual([
      '/rounds/9',
    ]);
    expect(routerUrl()).toBe('/matches');
  });

  it('pages Single Rounds alone: Next fetches Single Rounds page 2 and leaves Matches as they were', async () => {
    await open([matchRow({ id: 7 })], [roundRow({ id: 1, team_red_name: 'Page1' })], 1, 2);
    expect(paginator('matches')).toBeNull();
    expect(paginator('rounds')).not.toBeNull();

    await next('rounds');
    const req = roundsRequest();
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('20');
    expect(req.request.params.get('single_round')).toBe('true');
    http.expectNone((r) => r.url === `${BASE}/matches/`);
    req.flush(pageEnvelope([roundRow({ id: 2, team_red_name: 'Page2' })], 2, 2, 21));
    await harness.fixture.whenStable();

    expect(text(rows('rounds')[0])).toContain('Page2');
    expect(rows('matches').map((r) => r.querySelector('a')?.getAttribute('href'))).toEqual([
      '/matches/7',
    ]);
  });

  it('shows nothing empty while the lists are still loading', async () => {
    await harness.navigateByUrl('/matches');
    expect(text(page())).not.toContain('played yet');
    matchesRequest().flush(pageEnvelope([]));
    roundsRequest().flush(pageEnvelope([]));
    await harness.fixture.whenStable();
  });
});
