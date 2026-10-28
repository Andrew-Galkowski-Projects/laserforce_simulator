import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { League, LeagueState } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { deleteSummary, league, pageEnvelope } from '../testing/league-fixtures';
import { releaseLeaguesLoads } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

describe('Leagues list (/leagues, spec #409 F7)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

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
  });

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;

  function listRequest(state: LeagueState, pageNo = 1): TestRequest {
    return http.expectOne(
      (req) =>
        req.url === `${BASE}/leagues/` &&
        req.params.get('state') === state &&
        req.params.get('page') === String(pageNo),
    );
  }

  async function open(active: League[], archived: League[] = []): Promise<void> {
    await harness.navigateByUrl('/leagues');
    listRequest('active').flush(pageEnvelope(active, { page_size: 100 }));
    listRequest('archived').flush(pageEnvelope(archived, { page_size: 100 }));
    await harness.fixture.whenStable();
  }

  const table = (state: LeagueState): HTMLElement | null =>
    page().querySelector<HTMLElement>(`.league-table[data-state="${state}"]`);
  const rows = (state: LeagueState): HTMLElement[] => [
    ...(table(state)?.querySelectorAll<HTMLElement>('mat-row') ?? []),
  ];
  const cell = (r: HTMLElement, column: string): HTMLElement | null =>
    r.querySelector<HTMLElement>(`.mat-column-${column}`);
  const buttonIn = (el: Element | null, label: string): HTMLButtonElement | undefined =>
    [...(el?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);

  it('reads every active and every archived League, 100 a page', async () => {
    await harness.navigateByUrl('/leagues');
    for (const state of ['active', 'archived'] as const) {
      const req = listRequest(state);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('page_size')).toBe('100');
      req.flush(pageEnvelope([], { page_size: 100 }));
    }
    await harness.fixture.whenStable();
  });

  it('follows every page of a list (listAll)', async () => {
    await harness.navigateByUrl('/leagues');
    listRequest('active').flush(
      pageEnvelope([league({ id: 1, name: 'First' })], { page_size: 100, total_pages: 2 }),
    );
    listRequest('archived').flush(pageEnvelope([], { page_size: 100 }));
    listRequest('active', 2).flush(
      pageEnvelope([league({ id: 2, name: 'Second' })], { page: 2, page_size: 100 }),
    );
    await harness.fixture.whenStable();
    expect(rows('active').map((r) => text(cell(r, 'name')))).toEqual(['First', 'Second']);
  });

  it('shows Active and Archived tables with Name and State columns', async () => {
    await open(
      [league({ id: 1, name: 'Metro League' })],
      [league({ id: 2, name: 'Old League', state: 'archived' })],
    );
    const headings = [...page().querySelectorAll('h2')].map((h) => text(h));
    expect(headings).toEqual(['Active', 'Archived']);
    expect([...(table('active')?.querySelectorAll('mat-header-cell') ?? [])].map(text)).toEqual([
      'Name',
      'State',
    ]);
    expect(rows('active').map((r) => text(cell(r, 'name')))).toEqual(['Metro League']);
    expect(rows('archived').map((r) => text(cell(r, 'name')))).toEqual(['Old League']);
  });

  it('leaves out a table with no Leagues in it', async () => {
    await open([league()]);
    expect(table('active')).not.toBeNull();
    expect(table('archived')).toBeNull();
    expect([...page().querySelectorAll('h2')].map((h) => text(h))).toEqual(['Active']);
  });

  it("links each name to the League's dashboard", async () => {
    await open([league({ id: 7, name: 'Metro League' })]);
    const link = cell(rows('active')[0], 'name')?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/leagues/7');
    link?.click();
    await harness.fixture.whenStable();
    releaseLeaguesLoads();
    expect(routerUrl()).toBe('/leagues/7');
  });

  it.each([
    ['active', 'Active'],
    ['archived', 'Archived'],
  ] as const)('shows the %s state as a "%s" league-state badge', async (state, label) => {
    const l = league({ state });
    await open(state === 'active' ? [l] : [], state === 'archived' ? [l] : []);
    const badge = cell(rows(state)[0], 'state')?.querySelector<HTMLElement>('.league-state');
    expect(text(badge)).toBe(label);
    expect(badge?.dataset['state']).toBe(state);
  });

  it('offers Delete only on career (mode "league") rows', async () => {
    await open(
      [
        league({ id: 1, name: 'Career', mode: 'league' }),
        league({ id: 2, name: 'Sandbox', mode: 'sandbox' }),
        league({ id: 3, name: 'Multi', mode: 'multiplayer' }),
      ],
      [league({ id: 4, name: 'Old career', mode: 'league', state: 'archived' })],
    );
    const offered = (state: LeagueState) =>
      rows(state).map((r) => [text(cell(r, 'name')), buttonIn(r, 'Delete') !== undefined]);
    expect(offered('active')).toEqual([
      ['Career', true],
      ['Sandbox', false],
      ['Multi', false],
    ]);
    expect(offered('archived')).toEqual([['Old career', true]]);
  });

  it('deletes through the shared flow, then shows the list without the League', async () => {
    await open([league({ id: 1, name: 'Metro League' }), league({ id: 2, name: 'Rival' })]);
    buttonIn(rows('active')[0], 'Delete')?.click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/leagues/1/delete-summary/`).flush({ data: deleteSummary(), meta: {} });
    await harness.fixture.whenStable();
    const dialog = document.querySelector('mat-dialog-container');
    expect(text(dialog?.querySelector('h2'))).toBe('Delete "Metro League"?');
    buttonIn(dialog, 'Delete permanently')?.click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/leagues/1/`).flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();

    listRequest('active').flush(pageEnvelope([league({ id: 2, name: 'Rival' })]));
    listRequest('archived').flush(pageEnvelope([]));
    await harness.fixture.whenStable();
    expect(document.body.textContent).toContain('League "Metro League" deleted.');
    expect(routerUrl()).toBe('/leagues');
    expect(rows('active').map((r) => text(cell(r, 'name')))).toEqual(['Rival']);
  });

  it('shows a failed delete in the error box and keeps the row (I3)', async () => {
    await open([league({ id: 1, name: 'Metro League' })]);
    buttonIn(rows('active')[0], 'Delete')?.click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/leagues/1/delete-summary/`)
      .flush(
        { errors: [{ code: 'x', message: 'Database is down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toBe('Database is down.');
    expect(rows('active')).toHaveLength(1);
    expect(buttonIn(rows('active')[0], 'Delete')?.disabled).toBe(false);
  });

  it('says "No Leagues yet." when there are none', async () => {
    await open([], []);
    expect(text(page().querySelector('[role="status"]'))).toBe('No Leagues yet.');
    expect(page().querySelector('mat-table')).toBeNull();
  });

  it('shows no empty notice while Leagues are listed, nor before they arrive', async () => {
    await harness.navigateByUrl('/leagues');
    expect(page().textContent).not.toContain('No Leagues yet.');
    listRequest('active').flush(pageEnvelope([]));
    listRequest('archived').flush(pageEnvelope([league({ state: 'archived' })]));
    await harness.fixture.whenStable();
    expect(page().textContent).not.toContain('No Leagues yet.');
  });

  it('has a Create League link to /leagues/new', async () => {
    await open([]);
    const create = [...page().querySelectorAll('a')].find((a) => text(a) === 'Create League');
    expect(create?.getAttribute('href')).toBe('/leagues/new');
  });

  it('shows a failed load in the error box, and Retry reads the lists again (I2)', async () => {
    await harness.navigateByUrl('/leagues');
    listRequest('active').flush(
      { errors: [{ code: 'x', message: 'Database is down.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    http.match((req) => req.params.get('state') === 'archived');
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toContain('Database is down.');
    expect(page().textContent).not.toContain('No Leagues yet.');

    buttonIn(page(), 'Retry')?.click();
    await harness.fixture.whenStable();
    listRequest('active').flush(pageEnvelope([league()]));
    listRequest('archived').flush(pageEnvelope([]));
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(rows('active')).toHaveLength(1);
  });
});
