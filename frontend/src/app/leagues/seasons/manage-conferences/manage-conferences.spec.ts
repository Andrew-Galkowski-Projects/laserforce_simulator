import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { MatSelectHarness } from '@angular/material/select/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { ArenaMap } from '../../../api/maps.models';
import { ConferencesState } from '../../../api/seasons.models';
import { signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { editorMap } from '../../../maps/testing/editor-fixtures';
import { pageEnvelope } from '../../testing/league-fixtures';
import { failure } from '../../testing/league-stats-fixtures';
import { conferenceState, conferencesState, season, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';

const BASE = environment.apiBaseUrl;
const CONFERENCES_URL = `${BASE}/seasons/11/conferences/`;

/** The simulation-ready maps, by name. */
const MAPS: ArenaMap[] = [
  editorMap({ id: 4, name: 'Arena One' }),
  editorMap({ id: 9, name: 'Bunker' }),
];

const INTRO =
  "Conferences partition this Season's teams into self-contained regional leagues that each " +
  'play their own round-robin and standings. Leave it empty for a single flat league (the ' +
  'default).';

describe('Manage Conferences (/leagues/:id/seasons/:seasonId/conferences, spec #444)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => {
    http.match((req) => req.url.endsWith('/nav/'));
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector(
      'app-manage-conferences',
    ) as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const all = (selector: string): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(selector),
  ];
  const stable = (): Promise<void> => harness.fixture.whenStable();

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

  const nameInputs = (): HTMLInputElement[] => all('input.conference-name') as HTMLInputElement[];
  async function rename(index: number, value: string): Promise<void> {
    const input = nameInputs()[index];
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await stable();
  }

  /** The Team assignment rows as `[team, chosen option label]`. */
  const assignmentRows = (): [string, string][] =>
    all('.team-assignment tbody tr').map((tr) => {
      const select = tr.querySelector('select') as HTMLSelectElement;
      return [text(tr.querySelector('td')), text(select.selectedOptions[0])];
    });
  const teamSelect = (teamName: string): HTMLSelectElement => {
    const row = all('.team-assignment tbody tr').find(
      (tr) => text(tr.querySelector('td')) === teamName,
    );
    if (!row) throw new Error(`no ${teamName} row`);
    return row.querySelector('select') as HTMLSelectElement;
  };
  async function assign(teamName: string, optionLabel: string): Promise<void> {
    const select = teamSelect(teamName);
    const option = [...select.options].find((o) => text(o) === optionLabel);
    if (!option) throw new Error(`no ${optionLabel} option`);
    select.value = option.value;
    select.dispatchEvent(new Event('change'));
    await stable();
  }

  const conferencesRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'GET' && req.url === CONFERENCES_URL);
  const mapsRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'GET' && req.url === `${BASE}/maps/`);
  const saveRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'PUT' && req.url === CONFERENCES_URL);

  /** Lands on Season 11's Conferences screen and answers its GETs. */
  async function open(
    body: ConferencesState = conferencesState(),
    maps: ArenaMap[] = MAPS,
  ): Promise<void> {
    await harness.navigateByUrl(seasonUrl('conferences'));
    await releaseSeasonLayout(harness, season({ state: body.editable ? 'draft' : 'active' }));
    conferencesRequest().flush({ data: body, meta: {} });
    await stable();
    mapsRequest().flush(pageEnvelope(maps));
    await stable();
  }

  describe('header', () => {
    it('shows the breadcrumb, the h1 and the intro', async () => {
      await open();
      expect(text(q('nav[aria-label="Breadcrumb"]'))).toBe('Metro League › Season 1');
      expect(text(q('h1'))).toBe('Manage Conferences');
      expect(text(q('.manage-conferences__intro'))).toBe(INTRO);
    });

    it('says "No teams are enrolled in this Season yet." and nothing else without Teams', async () => {
      await open(conferencesState({ teams: [], conferences: [], unassigned_team_ids: [] }));
      expect(text(q('.manage-conferences__empty'))).toBe(
        'No teams are enrolled in this Season yet.',
      );
      expect(q('form')).toBeNull();
      expect(q('.manage-conferences__readonly')).toBeNull();
    });
  });

  describe('editable (draft Season)', () => {
    it('starts one row per Conference by ordinal, each with its name', async () => {
      await open(
        conferencesState({
          conferences: [
            conferenceState({ id: 32, name: 'West', ordinal: 2, team_ids: [7] }),
            conferenceState(),
          ],
          unassigned_team_ids: [8],
        }),
      );
      expect(text(q('form h2'))).toBe('Conferences');
      expect(nameInputs().map((i) => i.value)).toEqual(['East', 'West']);
      expect(nameInputs().map((i) => i.placeholder)).toEqual([
        'Conference name',
        'Conference name',
      ]);
      expect(buttons('Remove')).toHaveLength(2);
    });

    it('selects each Team\'s Conference, else "Unassigned", in `teams` order', async () => {
      await open();
      expect(text(all('form h2')[1])).toBe('Team assignment');
      expect(all('.team-assignment th').map(text)).toEqual(['Team', 'Conference']);
      expect(assignmentRows()).toEqual([
        ['Blue Bolts', 'East'],
        ['Gold', 'Unassigned'],
        ['Green Giants', 'Unassigned'],
        ['Red Raiders', 'East'],
      ]);
      expect([...teamSelect('Gold').options].map(text)).toEqual(['Unassigned', 'East']);
    });

    it('reads every simulation-ready map for the rotation picker', async () => {
      await harness.navigateByUrl(seasonUrl('conferences'));
      await releaseSeasonLayout(harness, season({ state: 'draft' }));
      conferencesRequest().flush({ data: conferencesState(), meta: {} });
      await stable();
      const req = mapsRequest();
      expect(req.request.params.get('simulation_ready')).toBe('true');
      expect(req.request.params.get('page_size')).toBe('100');
      req.flush(pageEnvelope(MAPS));
      await stable();
      await click('+ Add map');
      const picker = await loader.getHarness(MatSelectHarness);
      await picker.open();
      const options = await Promise.all((await picker.getOptions()).map((o) => o.getText()));
      expect(options).toEqual(['Arena One', 'Bunker']);
    });

    it("starts each row's composer from its rotation, in order", async () => {
      await open(
        conferencesState({ conferences: [conferenceState({ map_rotation_ids: [9, 4] })] }),
      );
      const pickers = await loader.getAllHarnesses(MatSelectHarness);
      expect(await Promise.all(pickers.map((p) => p.getValueText()))).toEqual([
        'Bunker',
        'Arena One',
      ]);
    });

    it('shows the rotation help text, without "Required" off rotate-by-Conference', async () => {
      await open();
      expect(text(q('.conference-row__help'))).toBe(
        'Author-ordered rotation for this conference, used when Map mode is "Rotate by ' +
          'Conference". A Round\'s map is rotation[matchday % len(rotation)].',
      );
      expect(q('.conference-row__required')).toBeNull();
    });

    it('adds the "Required" line under rotate-by-Conference', async () => {
      await open(conferencesState({ map_mode: 'rotate_by_conference' }));
      expect(text(q('.conference-row__required'))).toBe(
        "Required: this Season's map mode needs at least 1 map per conference.",
      );
    });

    it('"+ Add conference" appends an empty row, offered as "Conference <n>" while blank', async () => {
      await open();
      await click('+ Add conference');
      expect(nameInputs().map((i) => i.value)).toEqual(['East', '']);
      expect([...teamSelect('Gold').options].map(text)).toEqual([
        'Unassigned',
        'East',
        'Conference 2',
      ]);
      await rename(1, 'West');
      expect([...teamSelect('Gold').options].map(text)).toEqual(['Unassigned', 'East', 'West']);
    });

    it('removing a middle row unassigns its Teams and keeps every other Team on its Conference', async () => {
      await open(
        conferencesState({
          conferences: [
            conferenceState({ id: 31, name: 'East', ordinal: 1, team_ids: [3] }),
            conferenceState({ id: 32, name: 'North', ordinal: 2, team_ids: [5, 8] }),
            conferenceState({ id: 33, name: 'West', ordinal: 3, team_ids: [7] }),
          ],
          unassigned_team_ids: [],
        }),
      );
      await click('Remove', 1);
      expect(nameInputs().map((i) => i.value)).toEqual(['East', 'West']);
      expect(assignmentRows()).toEqual([
        ['Blue Bolts', 'Unassigned'],
        ['Gold', 'Unassigned'],
        ['Green Giants', 'West'],
        ['Red Raiders', 'East'],
      ]);
    });
  });

  describe('save', () => {
    const saveButton = (): HTMLButtonElement => button('Save Conferences');

    it('PUTs each row in order as {name, team_ids, map_rotation_ids}', async () => {
      await open();
      await click('+ Add conference');
      await rename(1, 'West');
      await assign('Gold', 'West');
      await assign('Green Giants', 'West');
      await click('+ Add map', 1);
      await click('+ Add map', 1);
      const pickers = await loader.getAllHarnesses(MatSelectHarness);
      await pickers[1].clickOptions({ text: 'Bunker' });
      await stable();
      await click('Save Conferences');
      const req = saveRequest();
      expect(req.request.body).toEqual({
        conferences: [
          { name: 'East', team_ids: [5, 3], map_rotation_ids: [] },
          { name: 'West', team_ids: [8, 7], map_rotation_ids: [4, 9] },
        ],
      });
      req.flush({ data: conferencesState(), meta: {} });
      await stable();
    });

    it('skips a rotation row with no map (no simulation-ready maps)', async () => {
      await open(conferencesState(), []);
      await click('+ Add map');
      await click('Save Conferences');
      const req = saveRequest();
      expect(req.request.body).toEqual({
        conferences: [{ name: 'East', team_ids: [5, 3], map_rotation_ids: [] }],
      });
      req.flush({ data: conferencesState(), meta: {} });
      await stable();
    });

    it('sends [] with no rows', async () => {
      await open();
      await click('Remove');
      await click('Save Conferences');
      const req = saveRequest();
      expect(req.request.body).toEqual({ conferences: [] });
      req.flush({ data: conferencesState({ conferences: [] }), meta: {} });
      await stable();
    });

    it('replaces the state from the response and says "Conferences saved."', async () => {
      await open();
      await click('Save Conferences');
      saveRequest().flush({
        data: conferencesState({
          conferences: [conferenceState({ name: 'Eastern', team_ids: [8] })],
          unassigned_team_ids: [3, 5, 7],
        }),
        meta: {},
      });
      await stable();
      expect(nameInputs().map((i) => i.value)).toEqual(['Eastern']);
      expect(assignmentRows()).toEqual([
        ['Blue Bolts', 'Unassigned'],
        ['Gold', 'Eastern'],
        ['Green Giants', 'Unassigned'],
        ['Red Raiders', 'Unassigned'],
      ]);
      expect(document.querySelector('mat-snack-bar-container')?.textContent).toContain(
        'Conferences saved.',
      );
    });

    it('lists every 400 error above the form and keeps the edits (I3)', async () => {
      await open();
      await rename(0, 'Renamed');
      await assign('Gold', 'Renamed');
      await click('Save Conferences');
      saveRequest().flush(
        {
          errors: [
            { code: 'invalid', message: 'Conference names must be unique.', field: 'conferences' },
            { code: 'invalid', message: 'Each conference needs a map.', field: 'conferences' },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      const alert = q('.manage-conferences__errors');
      expect(alert?.getAttribute('role')).toBe('alert');
      expect([...(alert?.querySelectorAll('li') ?? [])].map(text)).toEqual([
        'Conference names must be unique.',
        'Each conference needs a map.',
      ]);
      expect(alert?.nextElementSibling?.tagName).toBe('FORM');
      expect(nameInputs().map((i) => i.value)).toEqual(['Renamed']);
      expect(assignmentRows()[1]).toEqual(['Gold', 'Renamed']);
      expect(saveButton().disabled).toBe(false);
    });

    it('shows a 409 in the same list', async () => {
      await open();
      await click('Save Conferences');
      saveRequest().flush(
        ...failure(409, 'Conferences can only be edited while the Season is in draft.'),
      );
      await stable();
      expect([...all('.manage-conferences__errors li')].map(text)).toEqual([
        'Conferences can only be edited while the Season is in draft.',
      ]);
    });

    it('locks the whole form while the PUT is open (I3)', async () => {
      await open(conferencesState({ conferences: [conferenceState({ map_rotation_ids: [4] })] }));
      await click('Save Conferences');
      expect(saveButton().disabled).toBe(true);
      expect(nameInputs().every((i) => i.disabled)).toBe(true);
      expect(buttons('Remove')[0].disabled).toBe(true);
      expect(button('+ Add conference').disabled).toBe(true);
      expect(button('+ Add map').disabled).toBe(true);
      expect(button('Remove map').disabled).toBe(true);
      expect(all('.team-assignment select').every((s) => (s as HTMLSelectElement).disabled)).toBe(
        true,
      );
      saveRequest().flush({ data: conferencesState(), meta: {} });
      await stable();
      expect(saveButton().disabled).toBe(false);
      expect(nameInputs().every((i) => !i.disabled)).toBe(true);
    });

    it('links "Cancel" to the Season dashboard', async () => {
      await open();
      const cancel = [...page().querySelectorAll('a')].find((a) => text(a) === 'Cancel');
      expect(cancel?.getAttribute('href')).toBe('/leagues/1/seasons/11');
    });
  });

  describe('read-only (started Season)', () => {
    const LOCKED = 'The conference partition is locked once the Season starts.';
    const cards = (): HTMLElement[] => all('.conference-card');

    it('reads every map (not only simulation-ready ones) for the rotation names', async () => {
      await harness.navigateByUrl(seasonUrl('conferences'));
      await releaseSeasonLayout(harness, season({ state: 'active' }));
      conferencesRequest().flush({ data: conferencesState({ editable: false }), meta: {} });
      await stable();
      const req = mapsRequest();
      expect(req.request.params.has('simulation_ready')).toBe(false);
      req.flush(pageEnvelope(MAPS));
      await stable();
    });

    it('shows one card per Conference by ordinal: name, Teams by name, numbered rotation', async () => {
      await open(
        conferencesState({
          editable: false,
          conferences: [
            conferenceState({
              id: 32,
              name: 'West',
              ordinal: 2,
              team_ids: [7, 8],
              map_rotation_ids: [4],
              starting_map_rotation_ids: [9, 77, 4],
            }),
            conferenceState({ team_ids: [3, 5], map_rotation_ids: [4, 9] }),
          ],
          unassigned_team_ids: [],
        }),
      );
      expect(q('form')).toBeNull();
      expect(cards().map((c) => text(c.querySelector('h2')))).toEqual(['East', 'West']);
      expect(
        cards().map((c) => [...c.querySelectorAll('.conference-card__teams li')].map(text)),
      ).toEqual([
        ['Blue Bolts', 'Red Raiders'],
        ['Gold', 'Green Giants'],
      ]);
      expect(cards().map((c) => text(c.querySelector('.conference-card__rotation-label')))).toEqual(
        ['Map rotation', 'Map rotation'],
      );
      // West: the activation snapshot over the live list; map 77 (deleted) dropped.
      expect(
        cards().map((c) => [...c.querySelectorAll('ol.conference-card__rotation li')].map(text)),
      ).toEqual([
        ['Arena One', 'Bunker'],
        ['Bunker', 'Arena One'],
      ]);
      expect(text(q('.manage-conferences__locked'))).toBe(LOCKED);
    });

    it('says "No rotation maps." for a Conference without one', async () => {
      await open(conferencesState({ editable: false }));
      expect(q('ol.conference-card__rotation')).toBeNull();
      expect(text(q('.conference-card__no-rotation'))).toBe('No rotation maps.');
    });

    it('says there are no conferences for a flat Season', async () => {
      await open(
        conferencesState({ editable: false, conferences: [], unassigned_team_ids: [3, 5, 7, 8] }),
      );
      expect(cards()).toHaveLength(0);
      expect(text(q('.manage-conferences__flat'))).toBe(
        'This Season has no conferences (single flat league).',
      );
      expect(text(q('.manage-conferences__locked'))).toBe(LOCKED);
    });
  });

  describe('load (I2)', () => {
    const retry = (): HTMLButtonElement | undefined =>
      [...(q('[role="alert"]')?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry');

    it('shows a failed Conferences GET with Retry, which re-sends only that GET', async () => {
      await harness.navigateByUrl(seasonUrl('conferences'));
      await releaseSeasonLayout(harness, season({ state: 'draft' }));
      conferencesRequest().flush(...failure(500, 'Database is down.'));
      await stable();
      expect(text(q('[role="alert"] span'))).toBe('Database is down.');
      expect(http.match(`${BASE}/maps/`)).toHaveLength(0);

      retry()?.click();
      await stable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      conferencesRequest().flush({ data: conferencesState(), meta: {} });
      await stable();
      mapsRequest().flush(pageEnvelope(MAPS));
      await stable();
      expect(q('[role="alert"]')).toBeNull();
      expect(nameInputs().map((i) => i.value)).toEqual(['East']);
    });

    it('shows a failed maps GET with Retry, which re-sends only the maps GET', async () => {
      await harness.navigateByUrl(seasonUrl('conferences'));
      await releaseSeasonLayout(harness, season({ state: 'draft' }));
      conferencesRequest().flush({ data: conferencesState(), meta: {} });
      await stable();
      mapsRequest().flush(...failure(500, 'Maps are down.'));
      await stable();
      expect(text(q('[role="alert"] span'))).toBe('Maps are down.');
      expect(q('form')).toBeNull();

      retry()?.click();
      await stable();
      expect(http.match(CONFERENCES_URL)).toHaveLength(0);
      mapsRequest().flush(pageEnvelope(MAPS));
      await stable();
      expect(q('[role="alert"]')).toBeNull();
      expect(nameInputs().map((i) => i.value)).toEqual(['East']);
    });
  });
});
