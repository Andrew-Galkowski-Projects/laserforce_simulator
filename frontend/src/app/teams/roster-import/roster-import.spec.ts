import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

const TEAM: TeamListItem = {
  id: 7,
  name: 'Red Raiders',
  created_date: '2026-10-21T12:00:00Z',
  wins: 0,
  losses: 0,
  slot_commander: null,
  slot_heavy: null,
  slot_scout_1: null,
  slot_scout_2: null,
  slot_medic: null,
  slot_ammo: null,
  is_valid_roster: false,
  roster_errors: ['Missing Commander'],
  players: [],
};

const OTHER_TEAM: TeamListItem = { ...TEAM, id: 9, name: 'Blue Bolts' };

/** A jsdom-safe stand-in for the browser's object-URL pair, restored by `restore()`. */
function stubObjectUrls(): {
  create: ReturnType<typeof vi.fn>;
  revoke: ReturnType<typeof vi.fn>;
  restore: () => void;
} {
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  const create = vi.fn(() => 'blob:roster-template');
  const revoke = vi.fn();
  url['createObjectURL'] = create;
  url['revokeObjectURL'] = revoke;
  return {
    create,
    revoke,
    restore: () => {
      url['createObjectURL'] = before.create;
      url['revokeObjectURL'] = before.revoke;
    },
  };
}

describe('Import Roster page', () => {
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
  const routerUrl = (): string => TestBed.inject(Router).url;

  async function open(): Promise<void> {
    await harness.navigateByUrl('/teams/import');
    await harness.fixture.whenStable();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === label,
    );
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  function fileInput(): HTMLInputElement {
    const input = page().querySelector<HTMLInputElement>('input[type="file"][name="csv_file"]');
    if (!input) throw new Error('no csv_file input');
    return input;
  }

  /** Picks `file` the way the browser does: a `change` on the hidden input. */
  async function choose(file: File): Promise<void> {
    const input = fileInput();
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
    await harness.fixture.whenStable();
  }

  const csv = (name = 'roster.csv'): File =>
    new File(['team,name,role\nRed Raiders,Ace,commander\n'], name, { type: 'text/csv' });

  /** Submits the form and answers the import POST with `body` / `status`. */
  async function submit(body: object, status = 200): Promise<void> {
    button('Import').click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/teams/import/`)
      .flush(body, { status, statusText: status === 200 ? 'OK' : 'Bad Request' });
    await harness.fixture.whenStable();
  }

  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => a.textContent?.trim() === label);

  it('renders the intro, the picker and Cancel, without any request', async () => {
    await open();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Import Roster');
    expect(page().textContent).toContain('Upload a roster CSV (max 1000 rows)');
    expect(page().textContent).toContain(
      'team, name, role, age, started_playing_age, total_games, home_site, height',
    );
    expect(page().textContent).toContain('Roster CSV');
    expect(page().textContent).toContain('No file chosen');
    expect(fileInput().getAttribute('accept')).toBe('.csv,text/csv');
    expect(button('Choose CSV').type).toBe('button');
    expect(button('Import').type).toBe('submit');
    expect(button('Import').disabled).toBe(true);
    expect(link('Cancel')?.getAttribute('href')).toBe('/teams');
    expect(TestBed.inject(Title).getTitle()).toBe(`Import Roster - ${APP_TITLE}`);
  });

  it('shows the chosen filename and POSTs that file as multipart csv_file', async () => {
    await open();
    const file = csv('spring-roster.csv');
    await choose(file);
    expect(page().textContent).toContain('spring-roster.csv');
    expect(page().textContent).not.toContain('No file chosen');
    expect(button('Import').disabled).toBe(false);
    button('Import').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/teams/import/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBeInstanceOf(FormData);
    const form = req.request.body as FormData;
    expect([...form.keys()]).toEqual(['csv_file']);
    expect(form.get('csv_file')).toBe(file);
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  const fileError = (): string | undefined =>
    fileInput().closest('.roster-import__field')?.querySelector('mat-error')?.textContent?.trim();

  it('shows a csv_file error next to the file control, not in the alert line', async () => {
    await open();
    await choose(csv());
    await submit(
      {
        errors: [
          { code: 'invalid', message: 'The file must be UTF-8 encoded.', field: 'csv_file' },
        ],
        meta: {},
      },
      400,
    );
    expect(fileError()).toBe('The file must be UTF-8 encoded.');
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/teams/import');
  });

  const rowErrors = (): string[] =>
    [...page().querySelectorAll('.roster-import__rows li')].map((li) => li.textContent?.trim());

  it('lists row errors in the server order under the picker and keeps the chosen file', async () => {
    await open();
    await choose(csv('spring-roster.csv'));
    await submit(
      {
        errors: [
          { code: 'invalid', message: 'Unknown role "pilot".', field: 'row:1:role' },
          { code: 'invalid', message: 'Row has 7 columns, expected 8.', field: 'row:3' },
          { code: 'invalid', message: 'Missing column "role".', field: 'row:0:role' },
        ],
        meta: {},
      },
      400,
    );
    expect(rowErrors()).toEqual([
      'Row 1 (role): Unknown role "pilot".',
      'Row 3: Row has 7 columns, expected 8.',
      'File: Missing column "role".',
    ]);
    expect(page().textContent).toContain('spring-roster.csv');
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(fileError()).toBeUndefined();
  });

  const headings = (): string[] =>
    [...page().querySelectorAll('h2')].map((h) => h.textContent?.trim() ?? '');
  const teamLinks = (): [string, string | null][] =>
    [...page().querySelectorAll('ul a')].map((a) => [
      a.textContent?.trim() ?? '',
      a.getAttribute('href'),
    ]);

  it('swaps in the result with the created Teams as links, no appended list', async () => {
    await open();
    await choose(csv());
    await submit({
      data: {
        player_count: 6,
        row_count: 6,
        created_teams: [TEAM, OTHER_TEAM],
        appended_teams: [],
      },
      meta: {},
    });
    expect(routerUrl()).toBe('/teams/import');
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Import complete');
    expect(page().textContent).toContain('Imported 6 players across 6 rows.');
    expect(headings()).toEqual(['Created teams']);
    expect(teamLinks()).toEqual([
      ['Red Raiders', '/teams/7'],
      ['Blue Bolts', '/teams/9'],
    ]);
    expect(page().querySelector('input[type="file"]')).toBeNull();
    expect(link('Back to Teams')?.getAttribute('href')).toBe('/teams');
    expect(button('Import another').type).toBe('button');
    expect(document.body.querySelector('mat-snack-bar-container')).toBeNull();
  });

  it('shows only the appended list when no Team was created', async () => {
    await open();
    await choose(csv());
    await submit({
      data: { player_count: 2, row_count: 3, created_teams: [], appended_teams: [OTHER_TEAM] },
      meta: {},
    });
    expect(page().textContent).toContain('Imported 2 players across 3 rows.');
    expect(headings()).toEqual(['Appended to existing teams']);
    expect(teamLinks()).toEqual([['Blue Bolts', '/teams/9']]);
  });

  it('"Import another" shows the empty form again: no file, no errors', async () => {
    await open();
    await choose(csv('spring-roster.csv'));
    await submit(
      { errors: [{ code: 'invalid', message: 'Unknown role.', field: 'row:1:role' }], meta: {} },
      400,
    );
    expect(rowErrors()).toHaveLength(1);
    await submit({
      data: { player_count: 1, row_count: 1, created_teams: [TEAM], appended_teams: [] },
      meta: {},
    });
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Import complete');
    button('Import another').click();
    await harness.fixture.whenStable();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Import Roster');
    expect(page().textContent).toContain('No file chosen');
    expect(page().textContent).not.toContain('spring-roster.csv');
    expect(rowErrors()).toEqual([]);
    expect(fileError()).toBeUndefined();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(button('Import').disabled).toBe(true);
    expect(routerUrl()).toBe('/teams/import');
  });

  it('disables Import while the request is pending and enables it again after a failure', async () => {
    await open();
    await choose(csv());
    button('Import').click();
    await harness.fixture.whenStable();
    expect(button('Import').disabled).toBe(true);
    http
      .expectOne(`${BASE}/teams/import/`)
      .flush(
        { errors: [{ code: 'invalid', message: 'Nothing to import.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button('Import').disabled).toBe(false);
    expect(page().querySelector('[role="alert"]')?.textContent?.trim()).toBe('Nothing to import.');
  });

  it('clears the previous errors when the form is submitted again', async () => {
    await open();
    await choose(csv());
    await submit(
      {
        errors: [
          { code: 'invalid', message: 'Unknown role.', field: 'row:1:role' },
          { code: 'invalid', message: 'Nothing to import.', field: null },
        ],
        meta: {},
      },
      400,
    );
    expect(rowErrors()).toHaveLength(1);
    button('Import').click();
    await harness.fixture.whenStable();
    expect(rowErrors()).toEqual([]);
    expect(page().querySelector('[role="alert"]')).toBeNull();
    http
      .expectOne(`${BASE}/teams/import/`)
      .flush(
        { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
  });

  it('shows a failed template download in the alert line', async () => {
    await open();
    button('Download a template CSV').click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/teams/import/template/`)
      .flush(new Blob(['nope']), { status: 500, statusText: 'Server Error' });
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent?.trim()).toBe(
      'The server responded with 500.',
    );
  });

  it('downloads the template CSV through the API as roster_template.csv', async () => {
    await open();
    const urls = stubObjectUrls();
    const downloads: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloads.push(this.download);
    });
    try {
      button('Download a template CSV').click();
      await harness.fixture.whenStable();
      const req = http.expectOne(`${BASE}/teams/import/template/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.responseType).toBe('blob');
      expect(downloads).toEqual([]);
      req.flush(new Blob(['team,name\n'], { type: 'text/csv' }));
      await harness.fixture.whenStable();
      expect(urls.create).toHaveBeenCalledTimes(1);
      expect(downloads).toEqual(['roster_template.csv']);
      expect(urls.revoke).toHaveBeenCalledWith('blob:roster-template');
      expect(routerUrl()).toBe('/teams/import');
    } finally {
      click.mockRestore();
      urls.restore();
    }
  });
});
