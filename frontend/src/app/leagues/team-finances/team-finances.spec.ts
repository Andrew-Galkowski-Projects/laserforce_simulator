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
import { TeamFinances, TeamFinancesHeader } from '../../api/league-finances.models';
import { LeagueNav } from '../../api/leagues.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  financeLine,
  teamFinances,
  teamFinancesHeader,
  teamFinancesUpdate,
} from '../testing/league-finances-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { withTeamOptions } from '../testing/league-team-fixtures';
import { LeagueTeamFinances } from './team-finances';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/team/finances';
const financesUrl = (team: number): string => `${BASE}/leagues/1/teams/${team}/finances/`;
const DISABLED = 'Finances are disabled for this League.';
const NO_SEASON = 'No Season in this League yet — start a Season to view team finances.';
const NO_TEAM = "No team available to display in this League's current Season.";
const HYPE_TEXT =
  'The better you do in the regular season and the playoffs the higher your team hype. ' +
  'The higher your hype the more fans turn out and the more revenue your team gets.';
const SETTLED =
  'Finances are settled at the end of each Season with the ticket price and budget levels in ' +
  'effect then, so changes saved below show up once the current Season ends.';
const READ_ONLY = "Only your own team's expense settings can be changed.";
const BUDGET_FIELDS = ['budget_scouting', 'budget_coaching', 'budget_facilities', 'budget_health'];
const CONTROLS = [...BUDGET_FIELDS, 'injury_policy', 'ticket_price'];

describe('Team Finances (/leagues/:id/team/finances, spec #439)', () => {
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
    // Bolt (26) is the signed-in Account's Self-insert.
    await signIn({ ...USER, self_insert: { ...SELF_INSERT, id: 26 } });
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-league-team-finances ${selector}`);
  const qa = (selector: string): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(`app-league-team-finances ${selector}`),
  ];
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const financeRequests = (): TestRequest[] => http.match((req) => req.url.includes('/finances/'));

  async function navigate(path = PATH, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await harness.fixture.whenStable();
  }

  async function answer(
    team = 3,
    data: TeamFinances | TeamFinancesHeader = teamFinances(),
  ): Promise<TestRequest> {
    const req = http.expectOne((r) => r.method === 'GET' && r.url === financesUrl(team));
    req.flush(withTeamOptions(data));
    await harness.fixture.whenStable();
    return req;
  }

  async function open(data: TeamFinances | TeamFinancesHeader = teamFinances()): Promise<void> {
    await navigate();
    await answer(3, data);
  }

  it("asks for GET the nav's schedule Team's finances when no ?team= is given", async () => {
    await navigate();
    const req = await answer(3);
    expect(req.request.method).toBe('GET');
    expect(harness.fixture.debugElement.query(By.directive(LeagueTeamFinances))).not.toBeNull();
  });

  it('sends nothing while the nav is still loading', async () => {
    await harness.navigateByUrl(PATH);
    expect(financeRequests()).toEqual([]);
    expect(text(q('[role="status"]'))).toBe('');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    await harness.fixture.whenStable();
    await answer();
  });

  describe('I1: empty states, in order', () => {
    it('finances off in the nav: heads "<League> — Finances", says so, and sends no request', async () => {
      await navigate(PATH, leagueNav({ finance_enabled: false }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#team-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#team-finances-budget-form')).toBeNull();
    });

    it('finances off wins over no Season and no Team', async () => {
      await navigate(
        PATH,
        leagueNav({ finance_enabled: false, displayed_season: null, schedule_team: null }),
      );
      expect(financeRequests()).toEqual([]);
      expect(text(q('#team-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#team-finances-empty-notice')).toBeNull();
    });

    it('no displayed Season: heads "<League> — Finances", says so, and sends no request', async () => {
      await navigate(`${PATH}?team=3`, leagueNav({ displayed_season: null }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#team-finances-empty-notice'))).toBe(NO_SEASON);
      expect(q('app-team-picker')).toBeNull();
    });

    it('no resolvable Team: heads "<League> — Finances", says so, and sends no request', async () => {
      await navigate(PATH, leagueNav({ schedule_team: null }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#team-finances-empty-notice'))).toBe(NO_TEAM);
      expect(q('#team-finances-budget-form')).toBeNull();
    });

    it('a 404 on ?team= shows the no-Team state', async () => {
      await navigate(`${PATH}?team=99`);
      http.expectOne(financesUrl(99)).flush(...failure(404, 'Not found.'));
      await harness.fixture.whenStable();
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#team-finances-empty-notice'))).toBe(NO_TEAM);
      expect(q('[role="alert"]')).toBeNull();
    });

    it('a loaded finance_enabled: false also shows the disabled notice', async () => {
      await open(teamFinancesHeader());
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#team-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#team-finances-budget-form')).toBeNull();
    });
  });

  describe('I2: a failed load', () => {
    it('shows the error box with the API message; Retry re-sends the request', async () => {
      await navigate();
      http.expectOne(financesUrl(3)).flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const box = q('.stats-error');
      expect(box?.getAttribute('role')).toBe('alert');
      expect(text(box?.querySelector('span'))).toBe('Database is down.');
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(q('#team-finances-budget-form')).toBeNull();

      (box?.querySelector('button') as HTMLButtonElement).click();
      await harness.fixture.whenStable();
      await answer(3);
      expect(q('.stats-error')).toBeNull();
      expect(text(q('h1'))).toBe('Red Raiders — Finances');
    });

    it('a dropped connection shows the network message and Retry', async () => {
      await navigate();
      http.expectOne(financesUrl(3)).error(new ProgressEvent('error'));
      await harness.fixture.whenStable();
      expect(text(q('.stats-error span'))).toBe('Could not reach the server.');
      expect(text(q('.stats-error button'))).toBe('Retry');
    });
  });

  describe('Team picker', () => {
    const picker = (): HTMLSelectElement =>
      q('app-team-picker select#stats-team-select') as HTMLSelectElement;

    it('is the kit app-team-picker beside the loaded h1, labelled Team:, no All option', async () => {
      await open();
      expect(q('h1 ~ .stats-filters app-team-picker')).not.toBeNull();
      expect(text(q('label[for="stats-team-select"]'))).toBe('Team:');
      expect([...picker().options].map((o) => text(o))).toEqual(['Blue Bolts', 'Red Raiders']);
      expect(picker().selectedOptions[0].textContent?.trim()).toBe('Red Raiders');
    });

    it("asks for ?team='s Team", async () => {
      await navigate(`${PATH}?team=5`);
      await answer(5, teamFinances({ team: { id: 5, name: 'Blue Bolts' }, editable: false }));
      expect(text(q('h1'))).toBe('Blue Bolts — Finances');
    });

    it('sets ?team= and loads the picked Team', async () => {
      await open();
      picker().value = '5';
      picker().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      await answer(5, teamFinances({ team: { id: 5, name: 'Blue Bolts' }, editable: false }));
      expect(TestBed.inject(Router).url).toBe(`${PATH}?team=5`);
      expect(text(q('h1'))).toBe('Blue Bolts — Finances');
    });
  });

  describe('loaded: headline, tiles and note', () => {
    const tile = (key: string): string[] => [
      text(q(`#team-finances-tile-${key} .team-finances__tile-label`)),
      text(q(`#team-finances-tile-${key} .team-finances__tile-value`)),
    ];

    it('heads the page "<Team> — Finances"', async () => {
      await open();
      expect(text(q('h1'))).toBe('Red Raiders — Finances');
    });

    it('says "The current payroll is $X." with the figure in bold, then the hype paragraph', async () => {
      await open();
      expect(text(q('#team-finances-payroll-headline'))).toBe('The current payroll is $182.4k.');
      expect(text(q('#team-finances-payroll-headline strong'))).toBe('$182.4k');
      expect(text(q('#team-finances-hype-text'))).toBe(HYPE_TEXT);
    });

    it('shows Wins W–L, Hype to 2 dp, Revenue, Expenses from history[0] and Cash, in order', async () => {
      await open();
      expect(qa('#team-finances-metrics .team-finances__tile').map((t) => t.id)).toEqual([
        'team-finances-tile-wins',
        'team-finances-tile-hype',
        'team-finances-tile-revenue',
        'team-finances-tile-expenses',
        'team-finances-tile-cash',
      ]);
      expect(tile('wins')).toEqual(['Wins', '3–1']);
      expect(tile('hype')).toEqual(['Hype', '0.46']);
      expect(tile('revenue')).toEqual(['Revenue', '$250.3k']);
      expect(tile('expenses')).toEqual(['Expenses', '$257.5k']);
      expect(tile('cash')).toEqual(['Cash', '$120.0k']);
    });

    it('reads Revenue / Expenses from history[0], the newest settled Season', async () => {
      await open(
        teamFinances({
          history: [
            financeLine({ season: { id: 12, name: 'Season 2' }, revenue: 300, expenses: 200 }),
            financeLine(),
          ],
        }),
      );
      expect(tile('revenue')[1]).toBe('$300.0k');
      expect(tile('expenses')[1]).toBe('$200.0k');
      expect(text(q('#team-finances-metrics-note'))).toBe(
        `Revenue and Expenses are Season 2's final figures. ${SETTLED}`,
      );
    });

    it('shows — for Revenue and Expenses, and the not-settled note, with no history', async () => {
      await open(teamFinances({ history: [] }));
      expect(tile('revenue')[1]).toBe('—');
      expect(tile('expenses')[1]).toBe('—');
      expect(text(q('#team-finances-metrics-note'))).toBe(
        `No Season's finances have been settled yet. ${SETTLED}`,
      );
    });

    it("names history[0]'s Season in the note", async () => {
      await open();
      expect(text(q('#team-finances-metrics-note'))).toBe(
        `Revenue and Expenses are Season 1's final figures. ${SETTLED}`,
      );
    });

    describe('the Hype bar', () => {
      const fill = (): string =>
        (q('#team-finances-hype-bar .team-finances__bar-fill') as HTMLElement).style.width;

      it('fills current_hype x 100%', async () => {
        await open();
        expect(fill()).toBe('45.67%');
        expect(q('#team-finances-hype-bar')?.getAttribute('role')).toBe('progressbar');
      });

      it('clamps a hype above 1 to 100%', async () => {
        await open(teamFinances({ current_hype: 1.4 }));
        expect(fill()).toBe('100%');
        expect(q('#team-finances-hype-bar')?.getAttribute('aria-valuenow')).toBe('1');
      });

      it('clamps a negative hype to 0%', async () => {
        await open(teamFinances({ current_hype: -0.2 }));
        expect(fill()).toBe('0%');
      });
    });
  });

  describe('Player Salaries, availability and Season history', () => {
    const rows = (table: string): string[][] =>
      qa(`#${table} tbody tr`).map((tr) => [...tr.querySelectorAll('td')].map((td) => text(td)));

    it('lists Name / Salary with a Total = live_payroll footer, and the muted note', async () => {
      await open();
      expect(text(q('#team-finances-salaries h2'))).toBe('Player Salaries');
      expect(text(q('#team-finances-salaries-note'))).toBe(
        "Salaries are derived from each Player's overall rating (cap-scaled).",
      );
      expect(qa('#team-finances-salaries-table thead th').map((th) => text(th))).toEqual([
        'Name',
        'Salary',
      ]);
      expect(rows('team-finances-salaries-table')).toEqual([
        ['Ace', '$95.3k'],
        ['Bolt You', '$87.1k'],
      ]);
      expect(qa('#team-finances-salaries-table tfoot td').map((td) => text(td))).toEqual([
        'Total',
        '$182.4k',
      ]);
    });

    it('says "No roster players." in one row with an empty roster', async () => {
      await open(teamFinances({ roster_salaries: [] }));
      expect(rows('team-finances-salaries-table')).toEqual([['No roster players.']]);
    });

    it('puts the "You" badge on the Self-insert only, in both Player tables', async () => {
      await open();
      expect(qa('#team-finances-salaries-table .team-finances__you').map((b) => text(b))).toEqual([
        'You',
      ]);
      expect(text(q('#team-finances-salaries-table .team-finances__you')?.closest('td'))).toBe(
        'Bolt You',
      );
      expect(text(q('#team-finances-availability .team-finances__you')?.closest('td'))).toBe(
        'Bolt You',
      );
    });

    it('lists Player / Games remaining for injured players', async () => {
      await open();
      expect(text(q('#team-finances-availability-section h2'))).toBe('Player availability');
      expect(qa('#team-finances-availability thead th').map((th) => text(th))).toEqual([
        'Player',
        'Games remaining',
      ]);
      expect(rows('team-finances-availability')).toEqual([['Bolt You', '2']]);
    });

    it("says everyone's available with no injured players", async () => {
      await open(teamFinances({ availability: [] }));
      expect(q('#team-finances-availability table')).toBeNull();
      expect(text(q('#team-finances-availability-empty'))).toBe(
        "Everyone's available — no injured players.",
      );
    });

    it('lists Season history newest first: Season, Revenue, Expenses, Profit, Payroll, Hype', async () => {
      await open(
        teamFinances({
          history: [
            financeLine({ season: { id: 12, name: 'Season 2' }, hype: 0.5 }),
            financeLine(),
          ],
        }),
      );
      expect(text(q('#team-finances-history-section h2'))).toBe('Season history');
      expect(qa('#team-finances-table thead th').map((th) => text(th))).toEqual([
        'Season',
        'Revenue',
        'Expenses',
        'Profit',
        'Payroll',
        'Hype',
      ]);
      expect(rows('team-finances-table')).toEqual([
        ['Season 2', '$250.3k', '$257.5k', '-$7.2k', '$180.0k', '0.50'],
        ['Season 1', '$250.3k', '$257.5k', '-$7.2k', '$180.0k', '0.46'],
      ]);
    });

    it('says "No finance history yet — complete a Season." with no history', async () => {
      await open(teamFinances({ history: [] }));
      expect(rows('team-finances-table')).toEqual([
        ['No finance history yet — complete a Season.'],
      ]);
      expect(q('#team-finances-table tbody td')?.getAttribute('colspan')).toBe('6');
    });

    it('puts no "You" badge anywhere without a Self-insert on the roster', async () => {
      await open(
        teamFinances({
          roster_salaries: [teamFinances().roster_salaries[0]],
          availability: [],
        }),
      );
      expect(qa('.team-finances__you')).toEqual([]);
    });

    it('sits every table in a scrolling box', async () => {
      await open();
      for (const id of ['team-finances-salaries-table', 'team-finances-table']) {
        expect(q(`#${id}`)?.parentElement?.classList).toContain('stats-scroll');
      }
      expect(q('#team-finances-availability table')?.parentElement?.classList).toContain(
        'stats-scroll',
      );
    });
  });

  describe('Expense Settings', () => {
    const control = (name: string): HTMLInputElement | HTMLSelectElement => {
      const el = q(`#team-finances-budget-form [name="${name}"]`);
      if (!el) throw new Error(`no ${name} control`);
      return el as HTMLInputElement | HTMLSelectElement;
    };
    const field = (name: string): HTMLElement =>
      control(name).closest('mat-form-field') as HTMLElement;
    const save = (): HTMLButtonElement => q('#team-finances-budget-save') as HTMLButtonElement;
    const errorUnder = (name: string): string => text(field(name).querySelector('mat-error'));
    const patches = (): TestRequest[] => http.match((r) => r.method === 'PATCH');

    async function type(name: string, value: string): Promise<void> {
      const el = control(name);
      el.value = value;
      el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input'));
      await harness.fixture.whenStable();
    }

    async function submit(): Promise<void> {
      save().click();
      await harness.fixture.whenStable();
    }

    function expectPatch(): TestRequest {
      return http.expectOne((r) => r.method === 'PATCH' && r.url === financesUrl(3));
    }

    it('heads the form "Expense Settings" and ends it with "Save Expense Settings"', async () => {
      await open();
      expect(text(q('#team-finances-settings h2'))).toBe('Expense Settings');
      expect(text(save())).toBe('Save Expense Settings');
      expect(save().type).toBe('submit');
    });

    it('has the four Budget level rows with their saved levels and Django bounds', async () => {
      await open();
      expect(BUDGET_FIELDS.map((n) => text(field(n).querySelector('mat-label')))).toEqual([
        'Scouting',
        'Coaching',
        'Facilities',
        'Health',
      ]);
      expect(BUDGET_FIELDS.map((n) => control(n).value)).toEqual(['34', '34', '50', '20']);
      for (const name of BUDGET_FIELDS) {
        const input = control(name) as HTMLInputElement;
        expect(input.type).toBe('number');
        expect(input.getAttribute('min')).toBe('1');
        expect(input.getAttribute('max')).toBe('100');
        expect(input.getAttribute('step')).toBe('1');
        expect(input.required).toBe(true);
      }
    });

    it('takes max from max_level', async () => {
      await open(teamFinances({ max_level: 80 }));
      expect(control('budget_scouting').getAttribute('max')).toBe('80');
    });

    it("hints each Budget level's cost / level / neutral level; Health adds its clause", async () => {
      await open();
      expect(BUDGET_FIELDS.map((n) => text(field(n).querySelector('mat-hint')))).toEqual([
        '$10.0k / season · level 34 (neutral 34)',
        '$10.0k / season · level 34 (neutral 34)',
        '$14.8k / season · level 50 (neutral 34)',
        '$6.5k / season · level 20 (neutral 34) · shortens injury durations',
      ]);
      for (const name of CONTROLS) {
        expect(
          field(name).querySelector('.mat-mdc-form-field-subscript-dynamic-size'),
        ).not.toBeNull();
      }
    });

    it('has the Injury policy select (Auto-substitute / Play hurt) and its hint', async () => {
      await open();
      const select = control('injury_policy') as HTMLSelectElement;
      expect(select.tagName).toBe('SELECT');
      expect(text(field('injury_policy').querySelector('mat-label'))).toBe('Injury policy');
      expect([...select.options].map((o) => [o.value, text(o)])).toEqual([
        ['auto_sub', 'Auto-substitute'],
        ['play_hurt', 'Play hurt'],
      ]);
      expect(select.value).toBe('auto_sub');
      expect(text(field('injury_policy').querySelector('mat-hint'))).toBe(
        'how an injured starter is resolved at fixture time',
      );
    });

    it('has the Ticket price control (required, min 0, step 0.5) and its hint', async () => {
      await open();
      const input = control('ticket_price') as HTMLInputElement;
      expect(text(field('ticket_price').querySelector('mat-label'))).toBe('Ticket price');
      expect(input.type).toBe('number');
      expect(input.getAttribute('min')).toBe('0');
      expect(input.getAttribute('step')).toBe('0.5');
      expect(input.hasAttribute('max')).toBe(false);
      expect(input.required).toBe(true);
      expect(input.value).toBe('12.5');
      expect(text(field('ticket_price').querySelector('mat-hint'))).toBe(
        'drives attendance & gate revenue',
      );
    });

    it('starts the Ticket price at one decimal', async () => {
      await open(teamFinances({ ticket_price: 12 }));
      expect(control('ticket_price').value).toBe('12.0');
    });

    it('shows the form text and the live luxury tax line verbatim', async () => {
      await open();
      expect(text(q('#team-finances-form-text'))).toBe(
        'Budget levels are 1–100 (34 = neutral). Scouting & coaching are cost-only for now; ' +
          'facilities also feeds revenue.',
      );
      expect(text(q('#team-finances-live-penalties'))).toBe(
        'Live luxury tax $0.0k · min-payroll penalty $4.5k (computed from the current roster payroll).',
      );
    });

    it('Save PATCHes all six fields', async () => {
      await open();
      await type('budget_scouting', '40');
      await type('injury_policy', 'play_hurt');
      await submit();
      const req = expectPatch();
      expect(req.request.body).toEqual(teamFinancesUpdate());
      req.flush({ data: teamFinances(), meta: {} });
      await harness.fixture.whenStable();
    });

    it('sends the Ticket price as a number', async () => {
      await open(teamFinances({ ticket_price: 12 }));
      await submit();
      const req = expectPatch();
      expect(req.request.body.ticket_price).toBe(12);
      req.flush({ data: teamFinances(), meta: {} });
      await harness.fixture.whenStable();
    });

    describe('client validation blocks the PATCH', () => {
      it.each([
        ['budget_scouting', '', 'This field is required.'],
        ['budget_coaching', '0', 'Ensure this value is greater than or equal to 1.'],
        ['budget_facilities', '101', 'Ensure this value is less than or equal to 100.'],
        ['budget_health', '20.5', 'Enter a whole number.'],
        ['ticket_price', '', 'This field is required.'],
        ['ticket_price', '-0.5', 'Ensure this value is greater than or equal to 0.'],
      ])('%s = "%s" says "%s"', async (name, value, message) => {
        await open();
        await type(name, value);
        await submit();
        expect(patches()).toEqual([]);
        expect(errorUnder(name)).toBe(message);
      });
    });

    it('a 400 lands on its control, with no error box', async () => {
      await open();
      await submit();
      expectPatch().flush(
        {
          errors: [
            {
              code: 'max_value',
              message: 'Ensure this value is less than or equal to 100.',
              field: 'budget_scouting',
            },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await harness.fixture.whenStable();
      expect(errorUnder('budget_scouting')).toBe('Ensure this value is less than or equal to 100.');
      expect(q('#team-finances-form-errors')).toBeNull();
    });

    it('a 400 with no matching control lists under "Expense settings not saved:"', async () => {
      await open();
      await submit();
      expectPatch().flush(
        { errors: [{ code: 'invalid', message: 'Something is off.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await harness.fixture.whenStable();
      const box = q('#team-finances-form-errors');
      expect(box?.getAttribute('role')).toBe('alert');
      expect(text(box?.querySelector('.team-finances__errors-heading'))).toBe(
        'Expense settings not saved:',
      );
      expect([...(box?.querySelectorAll('li') ?? [])].map((li) => text(li))).toEqual([
        'Something is off.',
      ]);
    });

    it('a 409 lists in the error box, even one naming a field, prefixed with its label', async () => {
      await open();
      await submit();
      expectPatch().flush(
        {
          errors: [
            {
              code: 'team_not_editable',
              message: "Only the League's managed Team can be edited.",
              field: 'budget_scouting',
            },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await harness.fixture.whenStable();
      expect([...qa('#team-finances-form-errors li')].map((li) => text(li))).toEqual([
        "Scouting: Only the League's managed Team can be edited.",
      ]);
      expect(errorUnder('budget_scouting')).toBe('');
    });

    it('a 409 naming ticket_price reads "Ticket price: <message>"; a non-field one is bare', async () => {
      await open();
      await submit();
      expectPatch().flush(
        {
          errors: [
            { code: 'invalid', message: 'Too steep.', field: 'ticket_price' },
            { code: 'finance_disabled', message: 'Finances are off.', field: null },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await harness.fixture.whenStable();
      expect([...qa('#team-finances-form-errors li')].map((li) => text(li))).toEqual([
        'Ticket price: Too steep.',
        'Finances are off.',
      ]);
    });

    it('a 400 on a field with no control lists as "<raw field name>: <message>"', async () => {
      await open();
      await submit();
      expectPatch().flush(
        { errors: [{ code: 'invalid', message: 'Not allowed.', field: 'mystery' }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await harness.fixture.whenStable();
      expect([...qa('#team-finances-form-errors li')].map((li) => text(li))).toEqual([
        'mystery: Not allowed.',
      ]);
    });

    it('a resubmit clears the error box', async () => {
      await open();
      await submit();
      expectPatch().flush(...failure(409, 'Finances are disabled for this League.'));
      await harness.fixture.whenStable();
      expect(q('#team-finances-form-errors')).not.toBeNull();
      await submit();
      expect(q('#team-finances-form-errors')).toBeNull();
      expectPatch().flush({ data: teamFinances(), meta: {} });
      await harness.fixture.whenStable();
    });

    it('success: the "Expense settings saved." snackbar and the PATCH response\'s values', async () => {
      await open();
      await type('budget_scouting', '40');
      await submit();
      expectPatch().flush({
        data: teamFinances({
          budgets: { scouting: 40, coaching: 34, facilities: 50, health: 20 },
          budget_costs: { scouting: 12, coaching: 10, facilities: 14.75, health: 6.5 },
          injury_policy: 'play_hurt',
          ticket_price: 15,
          live_payroll: 200,
        }),
        meta: {},
      });
      await harness.fixture.whenStable();
      expect(document.querySelector('mat-snack-bar-container')?.textContent).toContain(
        'Expense settings saved.',
      );
      expect(text(field('budget_scouting').querySelector('mat-hint'))).toBe(
        '$12.0k / season · level 40 (neutral 34)',
      );
      expect(control('budget_scouting').value).toBe('40');
      expect(control('injury_policy').value).toBe('play_hurt');
      expect(control('ticket_price').value).toBe('15.0');
      expect(text(q('#team-finances-payroll-headline strong'))).toBe('$200.0k');
      // The picker keeps the GET's team_options.
      expect(qa('app-team-picker option').map((o) => text(o))).toEqual([
        'Blue Bolts',
        'Red Raiders',
      ]);
    });

    it('I3: every control and Save are disabled while the PATCH is open', async () => {
      await open();
      await submit();
      const req = expectPatch();
      expect(CONTROLS.map((n) => control(n).disabled)).toEqual(CONTROLS.map(() => true));
      expect(save().disabled).toBe(true);
      await submit();
      expect(patches()).toEqual([]);
      req.flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      expect(CONTROLS.map((n) => control(n).disabled)).toEqual(CONTROLS.map(() => false));
      expect(save().disabled).toBe(false);
    });

    it('destroying the screen while the PATCH is open cancels it: no snackbar', async () => {
      await open();
      await submit();
      const req = expectPatch();
      harness.fixture.destroy();
      expect(req.cancelled).toBe(true);
      expect(document.querySelector('mat-snack-bar-container')).toBeNull();
    });

    it('editable: no read-only notice, every control enabled', async () => {
      await open();
      expect(q('#team-finances-readonly-notice')).toBeNull();
      expect(CONTROLS.map((n) => control(n).disabled)).toEqual(CONTROLS.map(() => false));
    });

    it('editable: false says only your own team can be changed and disables every control + Save', async () => {
      await open(teamFinances({ editable: false }));
      expect(text(q('#team-finances-readonly-notice'))).toBe(READ_ONLY);
      expect(CONTROLS.map((n) => control(n).disabled)).toEqual(CONTROLS.map(() => true));
      expect(save().disabled).toBe(true);
      expect(control('budget_scouting').value).toBe('34');
    });

    it('editable: false never sends a PATCH, even on a direct form submit', async () => {
      await open(teamFinances({ editable: false }));
      await submit();
      q('#team-finances-budget-form')?.dispatchEvent(new Event('submit'));
      await harness.fixture.whenStable();
      expect(patches()).toEqual([]);
    });

    it('I3: a direct form submit while the PATCH is open sends no second PATCH', async () => {
      await open();
      await submit();
      const req = expectPatch();
      q('#team-finances-budget-form')?.dispatchEvent(new Event('submit'));
      await harness.fixture.whenStable();
      expect(patches()).toEqual([]);
      req.flush({ data: teamFinances(), meta: {} });
      await harness.fixture.whenStable();
    });

    it('switching to an editable Team after a read-only one enables the form again', async () => {
      await navigate(`${PATH}?team=5`);
      await answer(5, teamFinances({ team: { id: 5, name: 'Blue Bolts' }, editable: false }));
      await harness.navigateByUrl(`${PATH}?team=3`);
      await answer(3);
      expect(CONTROLS.map((n) => control(n).disabled)).toEqual(CONTROLS.map(() => false));
    });
  });
});
