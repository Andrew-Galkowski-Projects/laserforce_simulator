import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatCheckboxHarness } from '@angular/material/checkbox/testing';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { ArenaMap } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { editorMap } from '../../maps/testing/editor-fixtures';
import { leagueCreateResult, pageEnvelope } from '../testing/league-fixtures';
import { releaseLeaguesLoads } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

/** Simulation-ready maps, by name as the server orders them. */
const MAPS: ArenaMap[] = [
  editorMap({ id: 4, name: 'Arena One' }),
  editorMap({ id: 9, name: 'Bunker' }),
  editorMap({ id: 2, name: 'Warehouse' }),
];

/** What an untouched form posts: every required field, the Form's defaults otherwise. */
const DEFAULT_BODY = {
  league_name: 'Metro League',
  manager_team_name: '',
  difficulty: 'medium',
  season_name: 'Season 1',
  start_date: '2026-10-28',
  num_teams: 4,
  number_of_conferences: 0,
  mean: 50,
  std_dev: 15,
  league_spread: 'even',
  phases: 'round_robin:single_round_robin',
  map_mode: 'none',
  map_pool: [],
  map_rotation: '',
  finance_enabled: false,
  challenge_fired_luxury_tax: false,
};

describe('Advanced League create (/leagues/new/advanced)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

  beforeEach(async () => {
    // Only Date: zoneless `whenStable` keeps its real timers. 28 Oct 2026, noon local time.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 28, 12, 0, 0));
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
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => {
    vi.useRealTimers();
    http.verify();
  });

  const page = (): HTMLElement => {
    const host = harness.routeNativeElement?.querySelector<HTMLElement>(
      'app-league-create-advanced',
    );
    if (!host) throw new Error('not on Advanced create');
    return host;
  };
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));
  const fields = (label: string): Promise<MatFormFieldHarness[]> =>
    loader.getAllHarnesses(MatFormFieldHarness.with({ floatingLabelText: label }));
  const hasField = async (label: string): Promise<boolean> => (await fields(label)).length > 0;

  async function select(label: string, index = 0): Promise<MatSelectHarness> {
    const all = await fields(label);
    const control = await all[index]?.getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select #${index}`);
    return control;
  }

  async function optionTexts(label: string, index = 0): Promise<string[]> {
    const control = await select(label, index);
    await control.open();
    const texts = await Promise.all((await control.getOptions()).map((o) => o.getText()));
    await control.close();
    return texts;
  }

  async function choose(label: string, option: string, index = 0): Promise<void> {
    await (await select(label, index)).clickOptions({ text: option });
    await stable();
  }

  const input = (name: string): HTMLInputElement => {
    const found = page().querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (!found) throw new Error(`no ${name} input`);
    return found;
  };

  async function type(name: string, value: string): Promise<void> {
    const el = input(name);
    el.value = value;
    el.dispatchEvent(new Event('input'));
    await stable();
  }

  const checkbox = (label: string): Promise<MatCheckboxHarness> =>
    loader.getHarness(MatCheckboxHarness.with({ label }));

  function buttons(label: string): HTMLButtonElement[] {
    return [...page().querySelectorAll('button')].filter((b) => text(b) === label);
  }

  function button(label: string, index = 0): HTMLButtonElement {
    const found = buttons(label)[index];
    if (!found) throw new Error(`no ${label} button #${index}`);
    return found;
  }

  async function click(label: string, index = 0): Promise<void> {
    button(label, index).click();
    await stable();
  }

  const mapsRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'GET' && req.url === `${BASE}/maps/`);
  const createRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'POST' && req.url === `${BASE}/leagues/`);

  async function open(maps: ArenaMap[] = MAPS): Promise<void> {
    await harness.navigateByUrl('/leagues/new/advanced');
    mapsRequest().flush(pageEnvelope(maps));
    await stable();
  }

  async function submit(): Promise<void> {
    await click('Create League');
  }

  function flushCreated(req: TestRequest, id = 42): void {
    req.flush(
      { data: leagueCreateResult({ id }), meta: {} },
      { status: 201, statusText: 'Created' },
    );
  }

  function flushError(req: TestRequest, errors: object[]): void {
    req.flush({ errors, meta: {} }, { status: 400, statusText: 'Bad Request' });
  }

  describe('the map choices', () => {
    it('reads every simulation-ready map, by name', async () => {
      await harness.navigateByUrl('/leagues/new/advanced');
      expect(routerUrl()).toBe('/leagues/new/advanced');
      const req = mapsRequest();
      expect(req.request.params.get('simulation_ready')).toBe('true');
      expect(req.request.params.get('ordering')).toBe('name');
      expect(req.request.params.get('page_size')).toBe('100');
      req.flush(pageEnvelope(MAPS));
      await stable();
      expect(await optionTexts('Map pool')).toEqual(['Arena One', 'Bunker', 'Warehouse']);
      await click('+ Add map');
      expect(await optionTexts('Rotation map')).toEqual(['Arena One', 'Bunker', 'Warehouse']);
    });

    it('shows a failed map load in the alert', async () => {
      await harness.navigateByUrl('/leagues/new/advanced');
      mapsRequest().flush(
        { errors: [{ code: 'server_error', message: 'Maps are down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Maps are down.');
    });
  });

  describe('the fields (parity with CreateLeagueForm)', () => {
    it('heads the page and links to the template chooser and back to the list', async () => {
      await open();
      expect(text(page().querySelector('h1'))).toBe('Advanced — Create League');
      const links = [...page().querySelectorAll('a')];
      expect(links.find((a) => text(a) === 'Use a template instead')?.getAttribute('href')).toBe(
        '/leagues/new',
      );
      expect(links.find((a) => text(a) === 'Cancel')?.getAttribute('href')).toBe('/leagues');
    });

    it('bounds the text, date and number inputs as the Form does, with its defaults', async () => {
      await open();
      const leagueName = input('league_name');
      expect([leagueName.required, leagueName.maxLength, leagueName.value]).toEqual([
        true,
        100,
        '',
      ]);
      const teamName = input('manager_team_name');
      expect([teamName.required, teamName.maxLength, teamName.value]).toEqual([false, 100, '']);
      const seasonName = input('season_name');
      expect([seasonName.required, seasonName.maxLength, seasonName.value]).toEqual([
        true,
        100,
        'Season 1',
      ]);
      const startDate = input('start_date');
      expect([startDate.type, startDate.required, startDate.value]).toEqual([
        'date',
        true,
        '2026-10-28',
      ]);
      const mean = input('mean');
      expect([mean.type, mean.required, mean.min, mean.max, mean.value]).toEqual([
        'number',
        true,
        '0',
        '100',
        '50',
      ]);
      const stdDev = input('std_dev');
      expect([stdDev.type, stdDev.required, stdDev.min, stdDev.max, stdDev.value]).toEqual([
        'number',
        true,
        '1',
        '40',
        '15',
      ]);
    });

    it('offers the Form choices with their defaults', async () => {
      await open();
      const cases: [string, string[], string][] = [
        ['Number of teams', ['4', '8', '12', '16'], '4'],
        ['Conferences', ['None (single league)', '2', '3', '4'], 'None (single league)'],
        ['Difficulty', ['Easy', 'Medium', 'Hard'], 'Medium'],
        ['League spread', ['Even', 'Tiered', 'Steep'], 'Even'],
        [
          'Map mode',
          [
            '3-zone fallback',
            'Single map',
            'Random per Round',
            'Rotate by matchday',
            'Rotate by Conference',
          ],
          '3-zone fallback',
        ],
      ];
      for (const [label, options, value] of cases) {
        expect(await optionTexts(label)).toEqual(options);
        expect(await (await select(label)).getValueText()).toBe(value);
      }
      expect(await (await select('Map pool')).isMultiple()).toBe(true);
      expect(await (await checkbox('Enable team finances')).isChecked()).toBe(false);
      expect(await (await checkbox('Fire on luxury tax')).isChecked()).toBe(false);
    });

    it('leaves out the dormant visibility and the locked schedule format', async () => {
      await open();
      expect(await hasField('League visibility')).toBe(false);
      expect(page().querySelector('[name="visibility"], [name="schedule_format"]')).toBeNull();
      // The phase rows' own Schedule format is the only one on the page.
      expect((await fields('Schedule format')).length).toBe(1);
    });

    it('carries the Django help texts', async () => {
      await open();
      const hints = async (label: string): Promise<string[]> => (await field(label)).getTextHints();
      expect(await hints('Your team name')).toEqual([
        'Optional. Name your own team — it becomes one of the generated teams and your career ' +
          'team. Leave blank to keep its generated name.',
      ]);
      expect(await hints('Difficulty')).toEqual([
        'Picks which of the generated teams becomes your career team — Easy gives you the ' +
          'strongest, Medium the middle, Hard the weakest.',
      ]);
      expect(await hints('Conferences')).toEqual([
        'Split the teams into separate regional leagues that each play their own round-robin ' +
          'and standings. Pick a number to set them up after creating (needs at least 2 teams ' +
          'per conference); leave "None" for a single flat league.',
      ]);
      expect(await hints('League spread')).toEqual([
        'How unequal the generated league is. Even draws every team from the same stat ' +
          'distribution; Tiered and Steep ramp the teams from a strong projected favourite down ' +
          "to a weak projected wooden spoon, keeping the league's average talent the same.",
      ]);
      expect(await hints('Map mode')).toEqual([
        'Per-Season arena map configuration. Pick "3-zone fallback" to play every Round on the ' +
          'legacy 3-zone model, "Single map" to use one fixed map every Round, or "Random per ' +
          'Round" to draw a map per Round from the pool below.',
      ]);
      expect(await hints('Map pool')).toEqual([
        'Pool must be empty for "3-zone fallback", contain exactly 1 map for "Single map", or ' +
          'contain at least 1 map for "Random per Round".',
      ]);
      const body = text(page());
      expect(body).toContain(
        'Add a "Member night" block for a casual, per-Site social play interlude (excluded from ' +
          'the Standings).',
      );
      expect(body).toContain(
        'Author-ordered rotation, used when Map mode is "Rotate by matchday". A Round\'s map is ' +
          'rotation[matchday % len(rotation)] — keyed on the matchday alone, fully deterministic.',
      );
      expect(body).toContain(
        'Turn on the per-team budget & salary economy. Manager-set scouting / coaching / ' +
          'facilities budgets, derived salaries, and a season-level profit that feeds the ' +
          "owner's money mood.",
      );
      expect(body).toContain(
        'When on, the owner fires the manager outright any season the team pays the luxury tax ' +
          '— independent of cumulative mood. Only takes effect with team finances enabled.',
      );
    });

    it('lets every field grow for a wrapped hint or error (subscriptSizing dynamic)', async () => {
      await open();
      await choose('Phase type', 'Tournament');
      await click('+ Add map');
      const all = [...page().querySelectorAll('mat-form-field')];
      expect(all.length).toBeGreaterThan(15);
      for (const el of all) {
        expect(el.querySelector('.mat-mdc-form-field-subscript-dynamic-size')).not.toBeNull();
      }
    });

    it('gives the map pool and each rotation map a row of their own (name pickers get room)', async () => {
      await open();
      await click('+ Add map');
      for (const label of ['Map pool', 'Rotation map']) {
        const host = await (await field(label)).host();
        expect(await host.hasClass('picker-wide')).toBe(true);
      }
      const pool = page().querySelector('mat-form-field.picker-wide');
      expect(pool?.parentElement?.querySelectorAll('mat-form-field').length).toBe(1);
    });
  });

  describe('the request body', () => {
    it('sends every required field and the Form defaults for an untouched form', async () => {
      await open();
      await type('league_name', 'Metro League');
      await submit();
      const req = createRequest();
      expect(req.request.body).toEqual(DEFAULT_BODY);
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });

    it('sends what was picked, the pool as ids and the rotation as ordered comma-joined ids', async () => {
      await open();
      await type('league_name', 'Metro League');
      await type('manager_team_name', 'My Team');
      await type('season_name', 'Spring');
      await type('start_date', '2026-11-02');
      await choose('Number of teams', '8');
      await choose('Conferences', '2');
      await type('mean', '60');
      await type('std_dev', '20');
      await choose('Difficulty', 'Hard');
      await choose('League spread', 'Steep');
      await choose('Map mode', 'Rotate by matchday');
      await (await select('Map pool')).clickOptions({ text: 'Bunker' });
      await (await select('Map pool')).clickOptions({ text: 'Arena One' });
      await stable();
      await click('+ Add map');
      await click('+ Add map');
      await click('+ Add map');
      await choose('Rotation map', 'Warehouse', 0);
      await choose('Rotation map', 'Bunker', 1);
      // The third row keeps the first map, as a new <select> does.
      await (await checkbox('Enable team finances')).check();
      await (await checkbox('Fire on luxury tax')).check();
      await submit();
      const req = createRequest();
      expect(req.request.body).toEqual({
        league_name: 'Metro League',
        manager_team_name: 'My Team',
        difficulty: 'hard',
        season_name: 'Spring',
        start_date: '2026-11-02',
        num_teams: 8,
        number_of_conferences: 2,
        mean: 60,
        std_dev: 20,
        league_spread: 'steep',
        phases: 'round_robin:single_round_robin',
        map_mode: 'rotate_by_matchday',
        // A multiple mat-select keeps option order; the pool is a set on the server.
        map_pool: [4, 9],
        map_rotation: '2,9,4',
        finance_enabled: true,
        challenge_fired_luxury_tax: true,
      });
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });

    it('drops a removed rotation row and keeps the order of the rest', async () => {
      await open();
      await type('league_name', 'Metro League');
      await click('+ Add map');
      await click('+ Add map');
      await click('+ Add map');
      await choose('Rotation map', 'Warehouse', 0);
      await choose('Rotation map', 'Bunker', 1);
      await click('Remove map', 0);
      await submit();
      const req = createRequest();
      expect(req.request.body.map_rotation).toBe('9,4');
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });

    it('sends no rotation id when no map is simulation-ready', async () => {
      await open([]);
      await type('league_name', 'Metro League');
      await click('+ Add map');
      await submit();
      const req = createRequest();
      expect(req.request.body.map_rotation).toBe('');
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });

    it('a 201 opens the new League', async () => {
      await open();
      await type('league_name', 'Metro League');
      await submit();
      flushCreated(createRequest(), 42);
      await stable();
      expect(routerUrl()).toBe('/leagues/42');
      releaseLeaguesLoads();
    });
  });

  describe('the phase composer', () => {
    it('starts with one round-robin block, single round-robin', async () => {
      await open();
      expect((await fields('Phase type')).length).toBe(1);
      expect(await optionTexts('Phase type')).toEqual([
        'Round-robin',
        'Tournament',
        'Member night',
      ]);
      expect(await (await select('Phase type')).getValueText()).toBe('Round-robin');
      expect(await optionTexts('Schedule format')).toEqual([
        'Single round-robin',
        'Double round-robin',
      ]);
      expect(await hasField('Tournament mode')).toBe(false);
      expect(await hasField('Tournament format')).toBe(false);
    });

    it('a member night block shows no sub-config', async () => {
      await open();
      await choose('Phase type', 'Member night');
      for (const label of ['Schedule format', 'Tournament mode', 'Tournament format']) {
        expect(await hasField(label)).toBe(false);
      }
      expect(page().querySelector('input[name="phase_cut_0"]')).toBeNull();
    });

    it('a tournament block offers its modes, random draw disabled, and its formats', async () => {
      await open();
      await choose('Phase type', 'Tournament');
      expect(await hasField('Schedule format')).toBe(false);
      expect(await optionTexts('Tournament mode')).toEqual([
        'Season-ending (from standings)',
        'Mid-season (by team strength)',
        'Mid-season (random seed)',
        'Mid-season (drawn pool) — coming soon',
      ]);
      const modes = await select('Tournament mode');
      await modes.open();
      const disabled = await Promise.all((await modes.getOptions()).map((o) => o.isDisabled()));
      await modes.close();
      expect(disabled).toEqual([false, false, false, true]);
      expect(await optionTexts('Tournament format')).toEqual([
        'Single elimination',
        'Double elimination',
        'Round robin',
        'Round robin → Double elimination',
        'Swiss',
      ]);
      const cut = input('phase_cut_0');
      expect([cut.type, cut.min, cut.value]).toEqual(['number', '0', '0']);
    });

    it('shows each format its own sub-config', async () => {
      await open();
      await choose('Phase type', 'Tournament');
      const series = [
        'Final series length',
        'Semifinal series length',
        'Quarterfinal series length',
        'Earlier series length',
      ];
      const shown = async (): Promise<[boolean, boolean, boolean]> => [
        (await Promise.all(series.map(hasField))).every(Boolean),
        await hasField('Winners/Losers bracket advancers'),
        page().querySelector('input[name="phase_swiss_rounds_0"]') !== null,
      ];
      expect(await shown()).toEqual([true, false, false]);
      expect(await optionTexts('Final series length')).toEqual([
        'Best of 1',
        'Best of 3',
        'Best of 5',
      ]);
      await choose('Tournament format', 'Double elimination');
      expect(await shown()).toEqual([true, false, false]);
      await choose('Tournament format', 'Round robin → Double elimination');
      expect(await shown()).toEqual([true, true, false]);
      expect(await optionTexts('Winners/Losers bracket advancers')).toEqual([
        '4/0',
        '4/2',
        '8/0',
        '8/4',
        '16/0',
        '16/8',
      ]);
      await choose('Tournament format', 'Round robin');
      expect(await shown()).toEqual([false, false, false]);
      await choose('Tournament format', 'Swiss');
      expect(await shown()).toEqual([false, false, true]);
      const swiss = input('phase_swiss_rounds_0');
      expect([swiss.type, swiss.min, swiss.value]).toEqual(['number', '0', '0']);
    });

    it('sends the composed blocks as the template serialises them', async () => {
      await open();
      await type('league_name', 'Metro League');
      await choose('Schedule format', 'Double round-robin');
      await click('+ Add block');
      await choose('Phase type', 'Member night', 1);
      await click('+ Add block');
      await choose('Phase type', 'Tournament', 2);
      await choose('Tournament mode', 'Mid-season (by team strength)');
      await type('phase_cut_2', '8');
      await choose('Tournament format', 'Round robin → Double elimination');
      await choose('Final series length', 'Best of 5');
      await choose('Semifinal series length', 'Best of 3');
      await choose('Quarterfinal series length', 'Best of 3');
      await choose('Winners/Losers bracket advancers', '8/4');
      await submit();
      const req = createRequest();
      expect(req.request.body.phases).toBe(
        'round_robin:double_round_robin,member_night,' +
          'tournament:strength:8:round_robin_double_elim:5:3:3:1:8:4:0',
      );
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });

    it('drops a removed block', async () => {
      await open();
      await type('league_name', 'Metro League');
      await click('+ Add block');
      await choose('Phase type', 'Member night', 1);
      await click('Remove block', 0);
      expect((await fields('Phase type')).length).toBe(1);
      await submit();
      const req = createRequest();
      expect(req.request.body.phases).toBe('member_night');
      flushCreated(req);
      await stable();
      releaseLeaguesLoads();
    });
  });

  describe('validation and errors', () => {
    it('keeps out-of-range numbers and an empty name on the client, with the Form messages', async () => {
      await open();
      await type('mean', '101');
      await type('std_dev', '0');
      await type('season_name', '');
      await submit();
      http.expectNone((req) => req.method === 'POST');
      expect(await (await field('League name')).getTextErrors()).toEqual([
        'This field is required.',
      ]);
      expect(await (await field('Season name')).getTextErrors()).toEqual([
        'This field is required.',
      ]);
      expect(await (await field('Stat mean')).getTextErrors()).toEqual([
        'Ensure this value is less than or equal to 100.',
      ]);
      expect(await (await field('Stat standard deviation')).getTextErrors()).toEqual([
        'Ensure this value is greater than or equal to 1.',
      ]);
    });

    it('shows the clean() errors on their fields and the non-field ones in the alert', async () => {
      await open();
      await type('league_name', 'Metro League');
      await submit();
      flushError(createRequest(), [
        { code: 'invalid_choice', message: 'Select a valid choice.', field: 'map_mode' },
        {
          code: 'invalid',
          message: "Map pool must be empty when Map mode is 'Rotate by matchday'.",
          field: 'map_pool',
        },
        {
          code: 'invalid',
          message:
            "Map rotation must contain at least 1 map when Map mode is 'Rotate by matchday'.",
          field: 'map_rotation',
        },
        { code: 'invalid', message: 'Unknown phase type: bogus', field: 'phases' },
        {
          code: 'invalid',
          message: '2 conferences need at least 4 teams.',
          field: 'number_of_conferences',
        },
        { code: 'invalid', message: 'Something else broke.', field: null },
      ]);
      await stable();
      expect(await (await field('Map mode')).getTextErrors()).toEqual(['Select a valid choice.']);
      expect(await (await field('Map pool')).getTextErrors()).toEqual([
        "Map pool must be empty when Map mode is 'Rotate by matchday'.",
      ]);
      expect(await (await field('Conferences')).getTextErrors()).toEqual([
        '2 conferences need at least 4 teams.',
      ]);
      expect(text(page().querySelector('[data-error-for="map_rotation"]'))).toBe(
        "Map rotation must contain at least 1 map when Map mode is 'Rotate by matchday'.",
      );
      expect(text(page().querySelector('[data-error-for="phases"]'))).toBe(
        'Unknown phase type: bogus',
      );
      expect(text(page().querySelector('[data-error-for="non_field"]'))).toBe(
        'Something else broke.',
      );
      expect(routerUrl()).toBe('/leagues/new/advanced');
    });

    it('disables Create while in flight and keeps the input after a failure', async () => {
      await open();
      await type('league_name', 'Metro League');
      await choose('Phase type', 'Tournament');
      expect(button('Create League').disabled).toBe(false);
      await submit();
      expect(button('Create League').disabled).toBe(true);
      flushError(createRequest(), [{ code: 'invalid', message: 'Nope.', field: null }]);
      await stable();
      expect(button('Create League').disabled).toBe(false);
      expect(input('league_name').value).toBe('Metro League');
      expect(await (await select('Phase type')).getValueText()).toBe('Tournament');
    });

    it('clears the previous server errors on the next submit', async () => {
      await open();
      await type('league_name', 'Metro League');
      await submit();
      flushError(createRequest(), [
        { code: 'invalid', message: 'Unknown phase type: bogus', field: 'phases' },
        { code: 'invalid', message: 'Something else broke.', field: null },
      ]);
      await stable();
      await submit();
      expect(page().querySelector('[data-error-for]')).toBeNull();
      flushCreated(createRequest());
      await stable();
      releaseLeaguesLoads();
    });
  });
});
