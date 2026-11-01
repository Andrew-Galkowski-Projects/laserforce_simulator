import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { By } from '@angular/platform-browser';
import { environment } from '../../environments/environment';
import { League } from '../api/leagues.models';
import { signIn } from '../api/testing/auth-fixtures';
import { routes } from '../app.routes';
import { authInterceptor } from '../auth/auth.interceptor';
import { league, pageEnvelope } from '../leagues/testing/league-fixtures';
import { spacedText } from '../shared/testing/spaced-text';
import { Landing } from './landing';

const BASE = environment.apiBaseUrl;

describe('Landing (/, spec #460)', () => {
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
  const text = spacedText;

  function activeRequest(pageNo = 1): TestRequest {
    return http.expectOne(
      (req) =>
        req.method === 'GET' &&
        req.url === `${BASE}/leagues/` &&
        req.params.get('state') === 'active' &&
        req.params.get('page') === String(pageNo),
    );
  }

  async function open(active: League[]): Promise<void> {
    await harness.navigateByUrl('/');
    activeRequest().flush(pageEnvelope(active, { page_size: 100 }));
    await harness.fixture.whenStable();
  }

  const modeCard = (id: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`#mode-card-${id}`);
  const inProgress = (): HTMLElement | null => page().querySelector('#in-progress-leagues');
  const leagueCards = (): HTMLElement[] => [
    ...(inProgress()?.querySelectorAll<HTMLElement>('.landing__league') ?? []),
  ];
  const errorBox = (): HTMLElement | null => page().querySelector('[role="alert"]');

  it('renders Landing at / with the app name as its heading and tab title', async () => {
    await open([]);
    expect(harness.fixture.debugElement.query(By.directive(Landing))).not.toBeNull();
    expect(text(page().querySelector('h1'))).toBe('Laserforce Simulator');
    expect(TestBed.inject(Title).getTitle()).toBe('Laserforce Simulator');
  });

  it('links the Sandbox and League mode cards, with the template text', async () => {
    await open([]);
    const sandbox = modeCard('sandbox');
    expect(sandbox?.tagName).toBe('A');
    expect(sandbox?.getAttribute('href')).toBe('/teams');
    expect(text(sandbox)).toBe('Sandbox Quick one-off matches with custom teams.');
    const leagueCard = modeCard('league');
    expect(leagueCard?.tagName).toBe('A');
    expect(leagueCard?.getAttribute('href')).toBe('/leagues');
    expect(text(leagueCard)).toBe(
      'Single-player League Run a multi-Season campaign against the CPU.',
    );
  });

  it('shows Multiplayer as a disabled card with a Coming soon badge, not a link', async () => {
    await open([]);
    const multi = modeCard('multiplayer');
    expect(multi).not.toBeNull();
    expect(multi?.tagName).not.toBe('A');
    expect(multi?.querySelector('a')).toBeNull();
    expect(multi?.getAttribute('aria-disabled')).toBe('true');
    expect(text(multi)).toBe('Multiplayer Coming soon Play against other humans online.');
  });

  it('reads every active League, 100 a page, following total_pages', async () => {
    await harness.navigateByUrl('/');
    const first = activeRequest(1);
    expect(first.request.params.get('page_size')).toBe('100');
    first.flush(
      pageEnvelope([league({ id: 9, name: 'Nine' })], { page_size: 100, total_pages: 2 }),
    );
    activeRequest(2).flush(
      pageEnvelope([league({ id: 4, name: 'Four' })], { page: 2, page_size: 100, total_pages: 2 }),
    );
    await harness.fixture.whenStable();
    expect(leagueCards().map((c) => text(c.querySelector('.landing__card-title')))).toEqual([
      'Nine',
      'Four',
    ]);
  });

  it('shows one In Progress card per active League, linking to its dashboard', async () => {
    await open([
      league({ id: 7, name: 'Metro League', active_season: { id: 11, name: 'Season 2' } }),
      league({ id: 3, name: 'Old Town', active_season: null }),
    ]);
    expect(text(inProgress()?.querySelector('h2'))).toBe('In Progress');
    const [metro, old] = leagueCards();
    expect(metro.tagName).toBe('A');
    expect(metro.getAttribute('href')).toBe('/leagues/7');
    expect(text(metro)).toBe('Metro League Season: Season 2 Active');
    expect(old.getAttribute('href')).toBe('/leagues/3');
    expect(text(old)).toBe('Old Town No active season Active');
    expect(metro.querySelector('.league-state')?.getAttribute('data-state')).toBe('active');
  });

  it('hides the In Progress section when there is no active League', async () => {
    await open([]);
    expect(inProgress()).toBeNull();
    expect(page().textContent).not.toContain('In Progress');
    expect(errorBox()).toBeNull();
  });

  it('shows the load error with Retry, keeps the mode cards, and Retry clears the error', async () => {
    await harness.navigateByUrl('/');
    activeRequest().flush(
      { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await harness.fixture.whenStable();
    expect(text(errorBox())).toBe('Boom. Retry');
    expect(inProgress()).toBeNull();
    expect(modeCard('sandbox')).not.toBeNull();

    errorBox()?.querySelector('button')?.click();
    await harness.fixture.whenStable();
    expect(errorBox()).toBeNull();
    activeRequest().flush(pageEnvelope([league({ id: 5, name: 'Back' })], { page_size: 100 }));
    await harness.fixture.whenStable();
    expect(leagueCards().map((c) => c.getAttribute('href'))).toEqual(['/leagues/5']);
  });
});
