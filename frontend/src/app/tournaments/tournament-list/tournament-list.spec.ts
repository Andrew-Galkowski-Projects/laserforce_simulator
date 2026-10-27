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
import { signIn } from '../../api/testing/auth-fixtures';
import { Tournament } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

/** 09:05 on Oct 27, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const CREATED = new Date(2026, 9, 27, 9, 5).toISOString();

/** A `tournament_fields` row: a sandbox preset single-elimination Tournament in setup. */
function row(overrides: Partial<Tournament> = {}): Tournament {
  return {
    id: 1,
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
    created_at: CREATED,
    ...overrides,
  };
}

function envelope(items: Tournament[], page = 1, totalPages = 1, count = items.length) {
  return { data: items, meta: { page, page_size: 20, count, total_pages: totalPages } };
}

describe('Tournament list (/tournaments)', () => {
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
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  function listRequest(): TestRequest {
    return http.expectOne((req) => req.url === `${BASE}/tournaments/`);
  }

  async function open(items: Tournament[], totalPages = 1, count = items.length): Promise<void> {
    await harness.navigateByUrl('/tournaments');
    listRequest().flush(envelope(items, 1, totalPages, count));
    await harness.fixture.whenStable();
  }

  const rows = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('mat-row')];
  const cell = (r: HTMLElement, column: string): HTMLElement | null =>
    r.querySelector<HTMLElement>(`.mat-column-${column}`);

  it('asks for the first page of 20, newest first, with no filter', async () => {
    await harness.navigateByUrl('/tournaments');
    const req = listRequest();
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBeNull();
    expect(req.request.params.get('page_size')).toBe('20');
    expect(req.request.params.get('ordering')).toBe('-id');
    // In-League Tournaments stay listed, as on the Django page: no `embedded` filter.
    expect(req.request.params.keys().sort()).toEqual(['ordering', 'page_size']);
    req.flush(envelope([]));
    await harness.fixture.whenStable();
  });

  it('has Name, Format, State and Created columns', async () => {
    await open([row()]);
    const headers = [...page().querySelectorAll('mat-header-cell')].map((h) => text(h));
    expect(headers).toEqual(['Name', 'Format', 'State', 'Created']);
  });

  it('shows each Tournament in the order the server sent', async () => {
    await open([
      row({ id: 9, name: 'Winter Open' }),
      row({ id: 7, name: 'Autumn Cup' }),
      row({ id: 8, name: 'Bracket Bash' }),
    ]);
    expect(rows().map((r) => text(cell(r, 'name')))).toEqual([
      'Winter Open',
      'Autumn Cup',
      'Bracket Bash',
    ]);
  });

  it('renders a real-shaped row: name, format label, state label and created time', async () => {
    await open([
      row({
        id: 7,
        name: 'Random Draw Night',
        format: 'round_robin_double_elim',
        format_label: 'Random Draw',
        team_assembly: 'player_pool',
        state: 'active',
      }),
    ]);
    const [r] = rows();
    expect(text(cell(r, 'name'))).toBe('Random Draw Night');
    // The derived label, not the `format` key.
    expect(text(cell(r, 'format'))).toBe('Random Draw');
    expect(text(cell(r, 'state'))).toBe('Active');
    expect(text(cell(r, 'created'))).toBe('2026-10-27 09:05');
  });

  it.each([
    ['setup', 'Setup'],
    ['active', 'Active'],
    ['completed', 'Completed'],
  ] as const)('shows the %s state as a "%s" badge', async (state, label) => {
    await open([row({ state })]);
    const badge = cell(rows()[0], 'state')?.querySelector<HTMLElement>('.tournament-state');
    expect(badge).not.toBeNull();
    expect(text(badge)).toBe(label);
    expect(badge?.dataset['state']).toBe(state);
  });

  it('lists an in-League Tournament too', async () => {
    await open([
      row({ id: 3, name: 'Sandbox Cup' }),
      row({ id: 5, name: 'Season 1 Playoffs', embedded: true, season: { id: 2, name: 'S1' } }),
    ]);
    expect(rows().map((r) => text(cell(r, 'name')))).toEqual(['Sandbox Cup', 'Season 1 Playoffs']);
  });

  it("links each name to the Tournament's detail page", async () => {
    await open([row({ id: 3, name: 'Sandbox Cup' }), row({ id: 7, name: 'Autumn Cup' })]);
    const link = cell(rows()[1], 'name')?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/tournaments/7');
    link?.click();
    await harness.fixture.whenStable();
    http.match((req) => req.url !== `${BASE}/tournaments/`);
    expect(routerUrl()).toBe('/tournaments/7');
  });

  function link(label: string): HTMLAnchorElement {
    const a = [...page().querySelectorAll('a')].find((el) => text(el) === label);
    if (!a) throw new Error(`no "${label}" link`);
    return a;
  }

  it('has a Create Tournament button that opens /tournaments/new', async () => {
    await open([row()]);
    const create = link('Create Tournament');
    expect(create.getAttribute('href')).toBe('/tournaments/new');
    create.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/tournaments/new');
  });

  const EMPTY = 'No tournaments yet. Create one to get started.';

  it('shows the empty-state notice and still offers Create Tournament when there are none', async () => {
    await open([]);
    expect(rows()).toHaveLength(0);
    const notice = page().querySelector('[role="status"]');
    expect(text(notice)).toBe(EMPTY);
    expect(page().querySelector('mat-table')).toBeNull();
    expect(link('Create Tournament').getAttribute('href')).toBe('/tournaments/new');
  });

  it('shows no empty-state notice while Tournaments are listed', async () => {
    await open([row()]);
    expect(page().textContent).not.toContain(EMPTY);
  });

  it('shows nothing before the first page arrives', async () => {
    await harness.navigateByUrl('/tournaments');
    expect(page().textContent).not.toContain(EMPTY);
    expect(page().querySelector('mat-table')).toBeNull();
    listRequest().flush(envelope([]));
    await harness.fixture.whenStable();
  });

  const paginator = (): HTMLElement | null => page().querySelector('mat-paginator');

  it('shows no paginator when every Tournament fits on one page', async () => {
    await open([row()]);
    expect(paginator()).toBeNull();
  });

  it('shows a paginator past one page, and Next page fetches page 2', async () => {
    await open([row({ id: 30, name: 'Newest' })], 2, 21);
    expect(paginator()).not.toBeNull();

    paginator()?.querySelector<HTMLButtonElement>('button[aria-label="Next page"]')?.click();
    await harness.fixture.whenStable();
    const req = listRequest();
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('20');
    expect(req.request.params.get('ordering')).toBe('-id');
    req.flush(envelope([row({ id: 1, name: 'Oldest' })], 2, 2, 21));
    await harness.fixture.whenStable();

    expect(rows().map((r) => text(cell(r, 'name')))).toEqual(['Oldest']);
    expect(routerUrl()).toBe('/tournaments');
  });
});
