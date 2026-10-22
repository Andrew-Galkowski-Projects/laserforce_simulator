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
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

/** A `TeamListSerializer` row; slots and players default to an empty roster. */
function row(overrides: Partial<TeamListItem> = {}): TeamListItem {
  return {
    id: 1,
    name: 'Alpha',
    created_date: '2026-10-05T14:30:00Z',
    wins: 0,
    losses: 0,
    slot_commander: null,
    slot_heavy: null,
    slot_scout_1: null,
    slot_scout_2: null,
    slot_medic: null,
    slot_ammo: null,
    is_valid_roster: false,
    roster_errors: ['Missing Commander.'],
    players: [],
    ...overrides,
  };
}

function envelope(items: TeamListItem[], page = 1, totalPages = 1, count = items.length) {
  return { data: items, meta: { page, page_size: 100, count, total_pages: totalPages } };
}

describe('Teams list (/teams)', () => {
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

  function listRequest(): TestRequest {
    return http.expectOne((req) => req.url === `${BASE}/teams/`);
  }

  async function open(items: TeamListItem[], totalPages = 1): Promise<void> {
    await harness.navigateByUrl('/teams');
    listRequest().flush(envelope(items, 1, totalPages));
    await harness.fixture.whenStable();
  }

  it('asks for the first 100 Teams ordered by name', async () => {
    await harness.navigateByUrl('/teams');
    const req = listRequest();
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page_size')).toBe('100');
    expect(req.request.params.get('ordering')).toBe('name');
    expect(req.request.params.get('page')).toBeNull();
    req.flush(envelope([]));
    await harness.fixture.whenStable();
  });

  const cards = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('article')];
  const text = (el: Element | undefined): string => el?.textContent?.replace(/\s+/g, ' ') ?? '';

  it('shows each Team as a card with its name, active and bench counts and created date', async () => {
    // Four slots filled; players 11-14 slotted, 15 and 16 on the bench.
    const players = [11, 12, 13, 14, 15, 16].map((id) => ({
      id,
      name: `P${id}`,
      preferred_roles: [],
    }));
    await open([
      row({
        id: 7,
        name: 'Alpha',
        created_date: '2026-10-05T12:00:00Z',
        slot_commander: 11,
        slot_heavy: 12,
        slot_scout_1: 13,
        slot_medic: 14,
        players,
      }),
      row({ id: 8, name: 'Bravo', created_date: '2026-03-17T12:00:00Z' }),
    ]);
    expect(cards()).toHaveLength(2);
    expect(text(cards()[0])).toContain('Alpha');
    expect(text(cards()[0])).toContain('4/6 active (+2 bench)');
    expect(text(cards()[0])).toContain('Created: Oct 05, 2026');
    expect(text(cards()[1])).toContain('Bravo');
    expect(text(cards()[1])).toContain('0/6 active');
    expect(text(cards()[1])).not.toContain('bench');
    expect(text(cards()[1])).toContain('Created: Mar 17, 2026');
  });

  it('takes the Valid Roster / Incomplete badge from the server, not from the slots', async () => {
    const full = {
      slot_commander: 1,
      slot_heavy: 2,
      slot_scout_1: 3,
      slot_scout_2: 4,
      slot_medic: 5,
      slot_ammo: 6,
    };
    await open([
      row({ id: 1, name: 'Full but invalid', ...full, is_valid_roster: false }),
      row({ id: 2, name: 'Server says valid', is_valid_roster: true, roster_errors: [] }),
    ]);
    expect(text(cards()[0])).toContain('6/6 active');
    expect(text(cards()[0])).toContain('Incomplete');
    expect(text(cards()[0])).not.toContain('Valid Roster');
    expect(text(cards()[1])).toContain('Valid Roster');
    expect(text(cards()[1])).not.toContain('Incomplete');
  });

  function link(label: string, scope: HTMLElement = page()): HTMLAnchorElement {
    const a = [...scope.querySelectorAll('a')].find((el) => el.textContent?.trim() === label);
    if (!a) throw new Error(`no "${label}" link`);
    return a;
  }

  /** Clicks `a`; whatever the destination screen fetches is its own spec's business. */
  async function follow(a: HTMLAnchorElement): Promise<void> {
    a.click();
    await harness.fixture.whenStable();
    http.match((req) => req.url !== `${BASE}/teams/`);
  }

  it.each([
    ['View', '/teams/7'],
    ['Edit', '/teams/7/edit'],
  ])('opens %s for a Team at %s', async (label, url) => {
    await open([row({ id: 3, name: 'Alpha' }), row({ id: 7, name: 'Bravo' })]);
    await follow(link(label, cards()[1]));
    expect(routerUrl()).toBe(url);
  });

  it('has a header Create New Team action that opens /teams/new', async () => {
    await open([row()]);
    await follow(link('Create New Team'));
    expect(routerUrl()).toBe('/teams/new');
  });

  it('shows "No teams yet" with a Create Team button that opens /teams/new when there are none', async () => {
    await open([]);
    expect(cards()).toHaveLength(0);
    expect(page().textContent).toContain('No teams yet');
    await follow(link('Create Team'));
    expect(routerUrl()).toBe('/teams/new');
  });

  it('does not show the empty state while Teams are listed', async () => {
    await open([row()]);
    expect(page().textContent).not.toContain('No teams yet');
  });

  const paginator = (): HTMLElement | null => page().querySelector('mat-paginator');

  it('shows no paginator when every Team fits on one page', async () => {
    await open([row()], 1);
    expect(paginator()).toBeNull();
  });

  it('shows a paginator past one page, and Next page fetches page 2', async () => {
    await harness.navigateByUrl('/teams');
    listRequest().flush(envelope([row({ id: 1, name: 'Alpha' })], 1, 2, 101));
    await harness.fixture.whenStable();
    expect(paginator()).not.toBeNull();

    const next = paginator()?.querySelector<HTMLButtonElement>('button[aria-label="Next page"]');
    next?.click();
    await harness.fixture.whenStable();
    const req = listRequest();
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('100');
    expect(req.request.params.get('ordering')).toBe('name');
    req.flush(envelope([row({ id: 2, name: 'Zulu' })], 2, 2, 101));
    await harness.fixture.whenStable();

    expect(cards().map((c) => c.querySelector('h2')?.textContent)).toEqual(['Zulu']);
    expect(routerUrl()).toBe('/teams');
  });

  const headerLinks = (): [string, string | null][] =>
    [...(page().querySelector('.team-list__header')?.querySelectorAll('a') ?? [])].map((a) => [
      a.textContent?.trim() ?? '',
      a.getAttribute('href'),
    ]);

  it('has Generate Players and Import Roster in the header, left of Create New Team', async () => {
    await open([row()]);
    expect(headerLinks()).toEqual([
      ['Generate Players', '/teams/generate'],
      ['Import Roster', '/teams/import'],
      ['Create New Team', '/teams/new'],
    ]);
  });

  it.each([
    ['Generate Players', '/teams/generate'],
    ['Import Roster', '/teams/import'],
  ])('opens %s at %s from the header', async (label, url) => {
    await open([row()]);
    await follow(link(label));
    expect(routerUrl()).toBe(url);
  });

  it('keeps only Create Team in the empty state (no Generate / Import there)', async () => {
    await open([]);
    const empty = page().querySelector<HTMLElement>('.team-list__empty');
    expect(empty).not.toBeNull();
    const labels = [...(empty?.querySelectorAll('a') ?? [])].map((a) => a.textContent?.trim());
    expect(labels).toEqual(['Create Team']);
  });
});
