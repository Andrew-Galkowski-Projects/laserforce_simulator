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
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { LeagueTemplate } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueCreateResult, leagueTemplate } from '../testing/league-fixtures';
import { releaseLeaguesLoads } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

const TEMPLATES: LeagueTemplate[] = [
  leagueTemplate({ key: '4_team_quick', label: '4-Team Quick', num_teams: 4 }),
  leagueTemplate(),
  leagueTemplate({ key: '8_team_career', label: '8-Team Career', finance_enabled: true }),
];

const DIFFICULTY_HELP =
  'Picks which of the generated teams becomes your career team — Easy gives you the ' +
  'strongest, Medium the middle, Hard the weakest.';

describe('Create League (/leagues/new)', () => {
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
    const host = harness.routeNativeElement?.querySelector<HTMLElement>('app-league-create');
    if (!host) throw new Error('not on Create League');
    return host;
  };
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));

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

  const templatesRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'GET' && req.url === `${BASE}/leagues/templates/`);
  const createRequest = (): TestRequest =>
    http.expectOne((req) => req.method === 'POST' && req.url === `${BASE}/leagues/`);

  async function open(): Promise<void> {
    await harness.navigateByUrl('/leagues/new');
    templatesRequest().flush({ data: TEMPLATES, meta: {} });
    await stable();
  }

  async function submit(): Promise<void> {
    button('Create League').click();
    await stable();
  }

  function flushError(req: TestRequest, errors: object[]): void {
    req.flush({ errors, meta: {} }, { status: 400, statusText: 'Bad Request' });
  }

  it('offers the templates, the first picked, and Medium difficulty with its help text', async () => {
    await open();
    expect(text(page().querySelector('h1'))).toBe('Create League');
    expect(await optionTexts('League template')).toEqual([
      '4-Team Quick',
      '8-Team Classic',
      '8-Team Career',
    ]);
    expect(await (await select('League template')).getValueText()).toBe('4-Team Quick');
    expect(await (await field('League template')).getTextHints()).toEqual([
      "Each template sets the team count and the season's phase structure. Pick Advanced to " +
        'build your own.',
    ]);
    expect(await optionTexts('Difficulty')).toEqual(['Easy', 'Medium', 'Hard']);
    expect(await (await select('Difficulty')).getValueText()).toBe('Medium');
    expect(await (await field('Difficulty')).getTextHints()).toEqual([DIFFICULTY_HELP]);
  });

  it('links to the Advanced form', async () => {
    await open();
    const link = [...page().querySelectorAll('a')].find((a) => text(a) === 'Advanced…');
    expect(link?.getAttribute('href')).toBe('/leagues/new/advanced');
  });

  it('requires the League name, at most 100 characters, and keeps an empty one on the client', async () => {
    await open();
    expect(input('league_name').required).toBe(true);
    expect(input('league_name').maxLength).toBe(100);
    await submit();
    http.expectNone((req) => req.method === 'POST');
    expect(await (await field('League name')).getTextErrors()).toEqual(['This field is required.']);
  });

  it('posts {template, league_name, difficulty} and opens the new League on a 201', async () => {
    await open();
    await (await select('League template')).clickOptions({ text: '8-Team Career' });
    await type('league_name', 'Metro League');
    await (await select('Difficulty')).clickOptions({ text: 'Hard' });
    await submit();
    const req = createRequest();
    expect(req.request.body).toEqual({
      template: '8_team_career',
      league_name: 'Metro League',
      difficulty: 'hard',
    });
    req.flush(
      { data: leagueCreateResult({ id: 42 }), meta: {} },
      { status: 201, statusText: 'Created' },
    );
    await stable();
    expect(routerUrl()).toBe('/leagues/42');
    releaseLeaguesLoads();
  });

  it('maps a 400 onto the fields and the alert, and disables Create while in flight', async () => {
    await open();
    await type('league_name', 'Metro League');
    await submit();
    expect(button('Create League').disabled).toBe(true);
    flushError(createRequest(), [
      {
        code: 'max_length',
        message: 'Ensure this value has at most 100 characters (it has 101).',
        field: 'league_name',
      },
      {
        code: 'invalid_choice',
        message: 'Select a valid choice. gone is not one of the available choices.',
        field: 'template',
      },
      { code: 'invalid', message: 'Something else broke.', field: null },
    ]);
    await stable();
    expect(button('Create League').disabled).toBe(false);
    expect(await (await field('League name')).getTextErrors()).toEqual([
      'Ensure this value has at most 100 characters (it has 101).',
    ]);
    expect(await (await field('League template')).getTextErrors()).toEqual([
      'Select a valid choice. gone is not one of the available choices.',
    ]);
    expect(text(page().querySelector('[role="alert"]'))).toBe('Something else broke.');
    expect(input('league_name').value).toBe('Metro League');
    expect(routerUrl()).toBe('/leagues/new');
  });

  it('shows a failed template load in the alert', async () => {
    await harness.navigateByUrl('/leagues/new');
    templatesRequest().flush(
      { errors: [{ code: 'server_error', message: 'Templates are down.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await stable();
    expect(text(page().querySelector('[role="alert"]'))).toBe('Templates are down.');
  });
});
