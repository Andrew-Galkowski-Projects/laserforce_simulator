import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { TournamentDetail } from '../../api/tournaments.models';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { pageEnvelope } from '../../matches/testing/match-fixtures';
import { releaseTournamentsLoads } from '../testing/tournaments-loads';

const BASE = environment.apiBaseUrl;

function team(id: number, name: string, valid = true): TeamListItem {
  return {
    id,
    name,
    created_date: '2026-10-22T12:00:00Z',
    wins: 0,
    losses: 0,
    slot_commander: null,
    slot_heavy: null,
    slot_scout_1: null,
    slot_scout_2: null,
    slot_medic: null,
    slot_ammo: null,
    is_valid_roster: valid,
    roster_errors: valid ? [] : ['Missing Commander'],
    players: [],
  };
}

/** The server orders by name; Charlie's roster is incomplete. */
const TEAMS = [team(3, 'Alpha'), team(1, 'Bravo'), team(5, 'Charlie', false), team(2, 'Delta')];

/** A 201 body: the new Tournament's detail. */
function detail(id: number): TournamentDetail {
  return {
    id,
    name: 'Autumn Cup',
    format: 'single_elimination',
    format_label: 'Single elimination',
    state: 'setup',
    team_assembly: 'preset',
    role_assignment_mode: 'random',
    champion: null,
    embedded: false,
    season: null,
    final_series_length: 1,
    semifinal_series_length: 1,
    quarterfinal_series_length: 1,
    earlier_series_length: 1,
    wb_advancers: 0,
    lb_advancers: 0,
    swiss_rounds: 0,
    manager: 1,
    created_at: '2026-10-27T09:05:00Z',
    participants: [],
    pool_size: 0,
    is_drawn: false,
    stage: 'setup',
    brackets: [],
    rr_standings: null,
    swiss_standings: null,
    cut_labels: null,
  };
}

const SERIES_LABELS = [
  'Final series length',
  'Semifinal series length',
  'Quarterfinal series length',
  'Earlier rounds series length',
];

describe('Create Tournament (/tournaments/new)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

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
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => {
    const host = harness.routeNativeElement?.querySelector<HTMLElement>('app-tournament-create');
    if (!host) throw new Error('not on Create Tournament');
    return host;
  };
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));
  const hasField = async (label: string): Promise<boolean> =>
    (await loader.getHarnessOrNull(MatFormFieldHarness.with({ floatingLabelText: label }))) !==
    null;

  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }

  async function optionTexts(label: string): Promise<string[]> {
    const control = await select(label);
    await control.open();
    const texts = await Promise.all((await control.getOptions()).map((o) => o.getText()));
    await control.close();
    return texts;
  }

  async function choose(label: string, option: string): Promise<void> {
    await (await select(label)).clickOptions({ text: option });
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

  function button(label: string): HTMLButtonElement {
    const found = [...page().querySelectorAll('button')].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  function teamsRequest(): TestRequest {
    return http.expectOne((req) => req.method === 'GET' && req.url === `${BASE}/teams/`);
  }

  function createRequest(): TestRequest {
    return http.expectOne((req) => req.method === 'POST' && req.url === `${BASE}/tournaments/`);
  }

  async function open(teams: TeamListItem[] = TEAMS): Promise<void> {
    await harness.navigateByUrl('/tournaments/new');
    teamsRequest().flush(pageEnvelope(teams));
    await stable();
  }

  async function submit(): Promise<void> {
    button('Create Tournament').click();
    await stable();
  }

  function flushError(req: TestRequest, errors: object[]): void {
    req.flush({ errors, meta: {} }, { status: 400, statusText: 'Bad Request' });
  }

  describe('the Team picker', () => {
    it('asks for every Team by name and offers only the valid-roster ones', async () => {
      await harness.navigateByUrl('/tournaments/new');
      expect(routerUrl()).toBe('/tournaments/new');
      const req = teamsRequest();
      expect(req.request.params.get('ordering')).toBe('name');
      expect(req.request.params.get('page_size')).toBe('100');
      req.flush(pageEnvelope(TEAMS));
      await stable();
      expect(text(page().querySelector('h1'))).toBe('Create Tournament');
      expect(TestBed.inject(Title).getTitle()).toBe(`Create Tournament - ${APP_TITLE}`);
      expect(await optionTexts('Select existing teams')).toEqual(['Alpha', 'Bravo', 'Delta']);
      expect(text(page())).not.toContain('No teams available.');
      const cancel = [...page().querySelectorAll('a')].find((a) => text(a) === 'Cancel');
      expect(cancel?.getAttribute('href')).toBe('/tournaments');
    });

    it('shows the no-teams notice instead of the picker when no Team has a valid roster', async () => {
      await open([team(5, 'Charlie', false)]);
      expect(await hasField('Select existing teams')).toBe(false);
      expect(text(page().querySelector('.notice'))).toBe(
        'No teams available. Generate new teams below instead.',
      );
      expect(await hasField('Generate new teams')).toBe(true);
    });

    it('gives the Team picker a row of its own (name pickers get room)', async () => {
      await open();
      const host = await (await field('Select existing teams')).host();
      const el = page().querySelector('mat-form-field.picker-wide');
      expect(el).not.toBeNull();
      expect(await host.hasClass('picker-wide')).toBe(true);
      expect(el?.parentElement?.querySelectorAll('mat-form-field').length).toBe(1);
    });

    it('shows a failed Team load in the alert', async () => {
      await harness.navigateByUrl('/tournaments/new');
      flushError(teamsRequest(), [
        { code: 'server_error', message: 'Teams are down.', field: null },
      ]);
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Teams are down.');
    });
  });

  describe('the options', () => {
    it('carries the Django choices and defaults', async () => {
      await open();
      expect(await (await select('Team assembly')).getValueText()).toBe('Preset teams');
      expect(await optionTexts('Team assembly')).toEqual(['Preset teams', 'Player pool draw']);
      expect(await (await select('Bracket format')).getValueText()).toBe('Single elimination');
      expect(await optionTexts('Bracket format')).toEqual([
        'Single elimination',
        'Double elimination',
        'Round robin',
        'Round robin → Double elimination',
        'Swiss',
      ]);
      for (const label of SERIES_LABELS) {
        expect(await (await select(label)).getValueText()).toBe('Best of 1');
        expect(await optionTexts(label)).toEqual(['Best of 1', 'Best of 3', 'Best of 5']);
      }
      expect(input('generate_count').value).toBe('0');
      expect(input('generate_ppt').value).toBe('6');
      expect(text(page())).toContain(
        'Player pool draw: register individual players to a pool, then draw tier-balanced teams ' +
          'on the tournament page.',
      );
    });

    it('offers the six RR -> DE advancer combos, 4 WB / 0 LB first', async () => {
      await open();
      await choose('Bracket format', 'Round robin → Double elimination');
      expect(await (await select('Advancers (Winners / Losers bracket)')).getValueText()).toBe(
        '4 WB, 0 LB',
      );
      expect(await optionTexts('Advancers (Winners / Losers bracket)')).toEqual([
        '4 WB, 0 LB',
        '4 WB, 2 LB',
        '8 WB, 0 LB',
        '8 WB, 4 LB',
        '16 WB, 0 LB',
        '16 WB, 8 LB',
      ]);
      expect(text(page())).toContain(
        'Top RR-ranked teams seed the Winners bracket; the next seed the Losers bracket.',
      );
    });

    it('offers the two role-assignment modes for a Player pool, Random first', async () => {
      await open();
      await choose('Team assembly', 'Player pool draw');
      expect(await (await select('Role assignment (per Round)')).getValueText()).toBe(
        'Random per team per Round',
      );
      expect(await optionTexts('Role assignment (per Round)')).toEqual([
        'Random per team per Round',
        'Per-tier bijection (both teams)',
      ]);
    });

    it('bounds the inputs as the Django form does', async () => {
      await open();
      expect(input('name').required).toBe(true);
      expect(input('name').maxLength).toBe(100);
      expect([input('generate_count').type, input('generate_count').min]).toEqual(['number', '0']);
      expect([input('generate_ppt').type, input('generate_ppt').min]).toEqual(['number', '6']);
      await choose('Bracket format', 'Swiss');
      expect([input('swiss_rounds').type, input('swiss_rounds').min]).toEqual(['number', '0']);
      expect(input('swiss_rounds').value).toBe('0');
      expect(await (await field('Swiss rounds')).getTextHints()).toEqual(['0 = auto']);
    });

    it('lets every field grow for a wrapped hint or error (subscriptSizing dynamic)', async () => {
      const expectAllDynamic = (): void => {
        const fields = [...page().querySelectorAll('mat-form-field')];
        expect(fields.length).toBeGreaterThan(4);
        for (const el of fields) {
          expect(el.querySelector('.mat-mdc-form-field-subscript-dynamic-size')).not.toBeNull();
        }
      };
      await open();
      expectAllDynamic();
      await choose('Bracket format', 'Swiss');
      expect(await hasField('Swiss rounds')).toBe(true);
      expectAllDynamic();
      await choose('Team assembly', 'Player pool draw');
      expectAllDynamic();
    });
  });

  describe('the show / hide rules', () => {
    it('single elimination: series lengths shown; Swiss rounds, advancers and role mode hidden', async () => {
      await open();
      for (const label of SERIES_LABELS) expect(await hasField(label)).toBe(true);
      expect(await hasField('Swiss rounds')).toBe(false);
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(false);
      expect(await hasField('Role assignment (per Round)')).toBe(false);
      expect(await hasField('Select existing teams')).toBe(true);
      expect(await hasField('Generate new teams')).toBe(true);
      expect(await hasField('Players per generated team')).toBe(true);
    });

    it('double elimination keeps the series lengths', async () => {
      await open();
      await choose('Bracket format', 'Double elimination');
      for (const label of SERIES_LABELS) expect(await hasField(label)).toBe(true);
      expect(await hasField('Swiss rounds')).toBe(false);
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(false);
    });

    it('Swiss shows Swiss rounds and hides the series lengths and advancers', async () => {
      await open();
      await choose('Bracket format', 'Swiss');
      expect(await hasField('Swiss rounds')).toBe(true);
      for (const label of SERIES_LABELS) expect(await hasField(label)).toBe(false);
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(false);
    });

    it('round robin hides the series lengths, Swiss rounds and advancers', async () => {
      await open();
      await choose('Bracket format', 'Round robin');
      for (const label of SERIES_LABELS) expect(await hasField(label)).toBe(false);
      expect(await hasField('Swiss rounds')).toBe(false);
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(false);
    });

    it('RR -> DE shows the advancers and keeps the series lengths', async () => {
      await open();
      await choose('Bracket format', 'Round robin → Double elimination');
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(true);
      for (const label of SERIES_LABELS) expect(await hasField(label)).toBe(true);
      expect(await hasField('Swiss rounds')).toBe(false);
    });

    it('Player pool switches the format to RR -> DE, hides teams / generate, shows role mode', async () => {
      await open();
      await choose('Team assembly', 'Player pool draw');
      expect(await (await select('Bracket format')).getValueText()).toBe(
        'Round robin → Double elimination',
      );
      expect(await hasField('Select existing teams')).toBe(false);
      expect(await hasField('Generate new teams')).toBe(false);
      expect(await hasField('Players per generated team')).toBe(false);
      expect(await hasField('Role assignment (per Round)')).toBe(true);
      expect(await hasField('Advancers (Winners / Losers bracket)')).toBe(true);
    });

    it('Player pool hides the no-teams notice too', async () => {
      await open([]);
      expect(text(page())).toContain('No teams available.');
      await choose('Team assembly', 'Player pool draw');
      expect(text(page())).not.toContain('No teams available.');
    });

    it('back to Preset shows teams / generate again and hides role mode, keeping the format', async () => {
      await open();
      await choose('Team assembly', 'Player pool draw');
      await choose('Team assembly', 'Preset teams');
      expect(await hasField('Select existing teams')).toBe(true);
      expect(await hasField('Generate new teams')).toBe(true);
      expect(await hasField('Role assignment (per Round)')).toBe(false);
      expect(await (await select('Bracket format')).getValueText()).toBe(
        'Round robin → Double elimination',
      );
    });
  });

  describe('the request body', () => {
    it('preset single elimination: the visible fields only', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await (await select('Select existing teams')).clickOptions({ text: 'Alpha' });
      await (await select('Select existing teams')).clickOptions({ text: 'Delta' });
      await type('generate_count', '2');
      await choose('Final series length', 'Best of 3');
      await submit();
      const req = createRequest();
      expect(req.request.body).toEqual({
        name: 'Autumn Cup',
        format: 'single_elimination',
        team_assembly: 'preset',
        teams: [3, 2],
        generate_count: 2,
        generate_ppt: 6,
        final_series_length: 3,
        semifinal_series_length: 1,
        quarterfinal_series_length: 1,
        earlier_series_length: 1,
      });
      req.flush({ data: detail(42), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      releaseTournamentsLoads();
    });

    it('Player pool: no teams / generate_*, with role mode and the advancers', async () => {
      await open();
      await type('name', 'Draw Night');
      await (await select('Select existing teams')).clickOptions({ text: 'Alpha' });
      await type('generate_count', '3');
      await choose('Team assembly', 'Player pool draw');
      await choose('Role assignment (per Round)', 'Per-tier bijection (both teams)');
      await choose('Advancers (Winners / Losers bracket)', '8 WB, 4 LB');
      await submit();
      const req = createRequest();
      expect(req.request.body).toEqual({
        name: 'Draw Night',
        format: 'round_robin_double_elim',
        team_assembly: 'player_pool',
        role_assignment_mode: 'per_tier',
        rrde_combo: '8/4',
        final_series_length: 1,
        semifinal_series_length: 1,
        quarterfinal_series_length: 1,
        earlier_series_length: 1,
      });
      req.flush({ data: detail(43), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      releaseTournamentsLoads();
    });

    it('Swiss: Swiss rounds and no series lengths', async () => {
      await open();
      await type('name', 'Swiss Open');
      await type('generate_count', '4');
      await choose('Bracket format', 'Swiss');
      await type('swiss_rounds', '5');
      await submit();
      const req = createRequest();
      expect(req.request.body).toEqual({
        name: 'Swiss Open',
        format: 'swiss',
        team_assembly: 'preset',
        teams: [],
        generate_count: 4,
        generate_ppt: 6,
        swiss_rounds: 5,
      });
      req.flush({ data: detail(44), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      releaseTournamentsLoads();
    });

    it('a 201 opens the new Tournament', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await type('generate_count', '4');
      await submit();
      createRequest().flush({ data: detail(42), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(routerUrl()).toBe('/tournaments/42');
      releaseTournamentsLoads();
    });
  });

  describe('validation and errors', () => {
    it('keeps an empty name on the client with the Form message', async () => {
      await open();
      await submit();
      http.expectNone((req) => req.method === 'POST');
      expect(await (await field('Tournament name')).getTextErrors()).toEqual([
        'Tournament name is required.',
      ]);
    });

    it('keeps players per generated team under 6 on the client', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await type('generate_ppt', '5');
      await submit();
      http.expectNone((req) => req.method === 'POST');
      expect(await (await field('Players per generated team')).getTextErrors()).toEqual([
        'Ensure this value is greater than or equal to 6.',
      ]);
    });

    it('does not let a hidden field block the submit', async () => {
      await open();
      await type('name', 'Draw Night');
      await type('generate_ppt', '2');
      await choose('Team assembly', 'Player pool draw');
      await submit();
      createRequest().flush({ data: detail(43), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      releaseTournamentsLoads();
    });

    it('puts too_few_teams on the Teams picker and a non-field error in the alert', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await submit();
      flushError(createRequest(), [
        {
          code: 'too_few_teams',
          message: 'A tournament requires at least 4 teams.',
          field: 'teams',
        },
        { code: 'invalid', message: 'Something else broke.', field: null },
      ]);
      await stable();
      expect(await (await field('Select existing teams')).getTextErrors()).toEqual([
        'A tournament requires at least 4 teams.',
      ]);
      expect(text(page().querySelector('[role="alert"]'))).toBe('Something else broke.');
      expect(routerUrl()).toBe('/tournaments/new');
    });

    it('shows too_few_teams under the no-teams notice when there is no Team picker', async () => {
      await open([team(5, 'Charlie', false)]);
      await type('name', 'Autumn Cup');
      await submit();
      flushError(createRequest(), [
        {
          code: 'too_few_teams',
          message: 'A tournament requires at least 4 teams.',
          field: 'teams',
        },
      ]);
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toBe(
        'A tournament requires at least 4 teams.',
      );
    });

    it('disables Create while in flight and keeps the input after a failure', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await (await select('Select existing teams')).clickOptions({ text: 'Bravo' });
      await type('generate_count', '1');
      expect(button('Create Tournament').disabled).toBe(false);
      await submit();
      expect(button('Create Tournament').disabled).toBe(true);
      flushError(createRequest(), [
        {
          code: 'too_few_teams',
          message: 'A tournament requires at least 4 teams.',
          field: 'teams',
        },
      ]);
      await stable();
      expect(button('Create Tournament').disabled).toBe(false);
      expect(input('name').value).toBe('Autumn Cup');
      expect(input('generate_count').value).toBe('1');
      expect(await (await select('Select existing teams')).getValueText()).toBe('Bravo');
    });

    it('clears the previous server errors on the next submit', async () => {
      await open();
      await type('name', 'Autumn Cup');
      await submit();
      flushError(createRequest(), [
        {
          code: 'too_few_teams',
          message: 'A tournament requires at least 4 teams.',
          field: 'teams',
        },
        { code: 'invalid', message: 'Something else broke.', field: null },
      ]);
      await stable();
      await submit();
      expect(page().querySelector('[role="alert"]')).toBeNull();
      expect(await (await field('Select existing teams')).getTextErrors()).toEqual([]);
      createRequest().flush({ data: detail(42), meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      releaseTournamentsLoads();
    });
  });
});
