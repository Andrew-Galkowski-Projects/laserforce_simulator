import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { PlayoffsScreen, SeasonScope } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { Bracket } from '../../tournaments/tournament-results/bracket/bracket';
import {
  badParam,
  bracketNode,
  failure,
  playoffBracket,
  playoffsScreen,
  scopedEnvelope,
  seasonScope,
  worldsQualifier,
} from '../testing/league-stats-fixtures';
import { Playoffs } from './playoffs';
import { releaseLeagueChrome, releasePlayControls } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;
const PLAYOFFS = `${BASE}/leagues/1/playoffs/`;
const PATH = '/leagues/1/playoffs';

describe('Playoffs (/leagues/:id/playoffs, spec #417)', () => {
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

  afterEach(() => {
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-playoffs ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function playoffsRequest(): TestRequest {
    return http.expectOne((req) => req.url === PLAYOFFS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(
    data: PlayoffsScreen = playoffsScreen(),
    scope: SeasonScope = seasonScope(),
  ): Promise<Record<string, string>> {
    const req = playoffsRequest();
    const params = sent(req);
    req.flush(scopedEnvelope(data, { season_scope: scope }));
    await harness.fixture.whenStable();
    return params;
  }

  async function open(
    path = PATH,
    data?: PlayoffsScreen,
    scope?: SeasonScope,
  ): Promise<Record<string, string>> {
    await harness.navigateByUrl(path);
    releaseLeagueChrome();
    return answer(data, scope);
  }

  it('asks for the displayed Season with no params (no ordering, no paging)', async () => {
    expect(await open()).toEqual({});
    expect(harness.fixture.debugElement.query(By.directive(Playoffs))).not.toBeNull();
  });

  it("heads the page with the League's name", async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Playoffs');
  });

  describe('a bracket section', () => {
    const regional = (): PlayoffsScreen =>
      playoffsScreen({
        brackets: [
          playoffBracket({
            key: '2-31',
            name: 'East Playoffs',
            stage: 'regional_playoff',
            stage_label: 'Regional playoff',
            conference: { id: 31, name: 'East' },
          }),
        ],
      });

    it('shows its name, Conference and stage badge', async () => {
      await open(PATH, regional());
      expect(text(q('#league-playoffs-phase-2-31 h2'))).toBe('East Playoffs');
      expect(text(q('#league-playoffs-conference-2-31'))).toBe('East');
      expect(text(q('#league-playoffs-stage-2-31'))).toBe('Regional playoff');
    });

    it('leaves out the Conference and badge when the bracket has none', async () => {
      await open();
      expect(text(q('#league-playoffs-phase-2 h2'))).toBe('Playoffs');
      expect(q('#league-playoffs-conference-2')).toBeNull();
      expect(q('#league-playoffs-stage-2')).toBeNull();
    });

    it("draws the rounds through the tournaments' app-bracket, a column per round", async () => {
      await open(
        PATH,
        playoffsScreen({
          brackets: [
            playoffBracket({
              rounds: [
                {
                  bracket_round: 1,
                  nodes: [
                    bracketNode(),
                    bracketNode({
                      position: 1,
                      team_a: { id: 7, name: 'Green Giants' },
                      team_b: null,
                      seed_a: 3,
                      seed_b: null,
                      is_bye: true,
                      winner: { id: 7, name: 'Green Giants' },
                    }),
                  ],
                },
                {
                  bracket_round: 2,
                  nodes: [
                    bracketNode({
                      games: [{ game_number: 1, match_id: 88, score_a: 3, score_b: 1 }],
                    }),
                  ],
                },
              ],
            }),
          ],
        }),
      );
      const bracket = harness.fixture.debugElement.query(By.directive(Bracket));
      expect(bracket).not.toBeNull();
      const section = q('#league-playoffs-bracket-2')!;
      expect([...section.querySelectorAll('.bracket__round-name')].map(text)).toEqual([
        'Round 1',
        'Round 2',
      ]);
      // No level skipped: the round headings sit straight under the bracket's h2.
      expect(section.querySelectorAll('h3.bracket__round-name')).toHaveLength(2);
      const cards = [...section.querySelectorAll('app-node-card')];
      expect(cards).toHaveLength(3);
      expect(text(cards[0])).toContain('[1] Red Raiders');
      expect(text(cards[1])).toContain('Bye');
      const links = [...section.querySelectorAll<HTMLAnchorElement>('a')].map((a) =>
        a.getAttribute('href'),
      );
      expect(links).toContain('/teams/3/history');
      expect(links).toContain('/matches/88');
    });

    it("names a decided bracket's champion, linked to its history, above its rounds", async () => {
      await open(
        PATH,
        playoffsScreen({
          brackets: [playoffBracket({ champion: { id: 3, name: 'Red Raiders' } })],
        }),
      );
      const champion = q('#league-playoffs-champion-2')!;
      expect(text(champion)).toBe('Champion: Red Raiders');
      expect(champion.querySelector('strong a')!.getAttribute('href')).toBe('/teams/3/history');
      // playoffs.html still draws a decided bracket under its champion.
      expect(q('#league-playoffs-bracket-2 app-bracket')).not.toBeNull();
    });

    it('shows no champion while the bracket is undecided', async () => {
      await open();
      expect(q('#league-playoffs-champion-2')).toBeNull();
    });

    it('shows the not-seeded notice, and no bracket, for a pending bracket', async () => {
      await open(
        PATH,
        playoffsScreen({ brackets: [playoffBracket({ pending: true, rounds: [] })] }),
      );
      expect(text(q('#league-playoffs-pending-2'))).toBe(
        'The bracket is not seeded yet — the regular season is still in progress.',
      );
      expect(q('#league-playoffs-bracket-2')).toBeNull();
    });

    it('shows the field-not-set notice for a pending Last Chance bracket', async () => {
      await open(
        PATH,
        playoffsScreen({
          brackets: [
            playoffBracket({
              key: '2-31-lc',
              stage: 'last_chance',
              stage_label: 'Last Chance',
              pending: true,
              rounds: [],
            }),
          ],
        }),
      );
      expect(text(q('#league-playoffs-pending-2-31-lc'))).toBe(
        "The field is not set yet — this Conference's Regional playoff must crown its champion first.",
      );
      expect(q('#league-playoffs-bracket-2-31-lc')).toBeNull();
    });

    it('shows one section per bracket, in the order the API sent', async () => {
      await open(
        PATH,
        playoffsScreen({
          brackets: [
            playoffBracket({ key: '2-31', name: 'East Playoffs' }),
            playoffBracket({ key: '2-worlds', name: 'Worlds' }),
          ],
        }),
      );
      expect([...page().querySelectorAll('app-playoffs section h2')].map(text)).toEqual([
        'East Playoffs',
        'Worlds',
      ]);
    });
  });

  describe('Worlds qualification', () => {
    const withQualifiers = (): PlayoffsScreen =>
      playoffsScreen({
        worlds_qualifiers: [
          worldsQualifier(),
          worldsQualifier({
            seed: 2,
            team_id: 5,
            team_name: 'Blue Bolts',
            conference_name: 'West',
            provenance: 'last_chance',
            provenance_label: 'Last Chance',
          }),
        ],
      });

    it('has the Seed, Team, Conference and Provenance columns', async () => {
      await open(PATH, withQualifiers());
      expect(text(q('#league-playoffs-worlds h2'))).toBe('Worlds qualification');
      expect([...page().querySelectorAll('#league-playoffs-worlds-table th')].map(text)).toEqual([
        'Seed',
        'Team',
        'Conference',
        'Provenance',
      ]);
    });

    it('shows a row per qualifier, the Team linked to its history', async () => {
      await open(PATH, withQualifiers());
      const cells = (seed: number): string[] =>
        [...page().querySelectorAll(`#league-playoffs-worlds-row-${seed} td`)].map(text);
      expect(cells(1)).toEqual(['1', 'Red Raiders', 'East', 'Conference champion']);
      expect(cells(2)).toEqual(['2', 'Blue Bolts', 'West', 'Last Chance']);
      expect(q('#league-playoffs-worlds-team-2 a')!.getAttribute('href')).toBe('/teams/5/history');
      expect(q('#league-playoffs-worlds-row-1 td')!.classList).toContain('num');
    });

    it('is left out when no Team has qualified', async () => {
      await open();
      expect(q('#league-playoffs-worlds')).toBeNull();
    });

    it('sits after the bracket sections', async () => {
      await open(PATH, withQualifiers());
      const ids = [...page().querySelectorAll('app-playoffs section')].map((s) => s.id);
      expect(ids).toEqual(['league-playoffs-phase-2', 'league-playoffs-worlds']);
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no picker, for a League with no Season', async () => {
      await open(
        PATH,
        playoffsScreen({ season: null, brackets: [] }),
        seasonScope({ selected: null, options: [] }),
      );
      expect(text(q('#league-playoffs-empty-notice'))).toBe(
        'No Season in this League yet — start a Season to set up playoffs.',
      );
      expect(q('#stats-season-select')).toBeNull();
    });

    it('shows the no-tournament-phase notice for a Season with no brackets', async () => {
      await open(PATH, playoffsScreen({ brackets: [] }));
      expect(text(q('#league-playoffs-empty-notice'))).toBe(
        'This Season has no tournament phase. Compose a Season with a tournament phase to see its bracket here.',
      );
      expect(q('section')).toBeNull();
    });

    it('shows no notice while there are brackets', async () => {
      await open();
      expect(q('#league-playoffs-empty-notice')).toBeNull();
    });
  });

  describe('Season picker', () => {
    const seasonSelect = (): HTMLSelectElement =>
      page().querySelector<HTMLSelectElement>('#stats-season-select')!;

    it('lists the Seasons as "name (year)" with no Career option', async () => {
      await open();
      expect([...seasonSelect().options].map(text)).toEqual(['Season 1 (2026)', 'Season 0 (2025)']);
      expect(seasonSelect().value).toBe('11');
    });

    it('a Season change navigates and reloads that Season', async () => {
      await open();
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      seasonSelect().value = '10';
      seasonSelect().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/playoffs?season=10');
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([false]);
      expect(await answer()).toEqual({ season: '10' });
    });

    it('reads the Season from the URL', async () => {
      expect(await open('/leagues/1/playoffs?season=10')).toEqual({ season: '10' });
    });

    it('never sends a hand-edited ?season=career (no Career on Playoffs)', async () => {
      expect(await open('/leagues/1/playoffs?season=career')).toEqual({});
    });
  });

  it('drops a stale season the API rejects, replacing the URL, and reloads (I2)', async () => {
    await harness.navigateByUrl('/leagues/1/playoffs?season=99');
    releaseLeagueChrome();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
    playoffsRequest().flush(...badParam('season'));
    await harness.fixture.whenStable();
    expect(url()).toBe('/leagues/1/playoffs');
    expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
    expect(await answer()).toEqual({});
    expect(q('[role="alert"]')).toBeNull();
    expect(text(q('#league-playoffs-phase-2 h2'))).toBe('Playoffs');
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await harness.navigateByUrl(PATH);
    releaseLeagueChrome();
    playoffsRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('section')).toBeNull();
    expect(url()).toBe(PATH);
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    releasePlayControls();
    http.match((req) => req.url === PLAYOFFS);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-playoffs')).toBeNull();
  });
});
