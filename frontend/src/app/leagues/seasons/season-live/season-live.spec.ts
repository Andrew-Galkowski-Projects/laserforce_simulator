import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By, Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { signIn } from '../../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../../app-title';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { Playback } from '../../../matches/playback/playback';
import { roundDetail } from '../../../matches/testing/match-fixtures';
import { roundPlayback } from '../../../matches/testing/playback-fixtures';
import { timeline } from '../../../matches/testing/timeline-fixtures';

import { seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';
import { SeasonLive } from './season-live';

const BASE = environment.apiBaseUrl;
const NO_GAME = 'No live game to watch — start one from the Play menu.';
const FOOTER = 'The rest of the week plays in the background — results are final.';

describe('One Week (Live) (/leagues/:id/seasons/:seasonId/live, spec #454)', () => {
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
    // A Round switch cancels the old Round's three GETs.
    http.verify({ ignoreCancelled: true });
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector('app-season-live') as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const roundButton = (label: string): HTMLButtonElement | undefined =>
    [...page().querySelectorAll<HTMLButtonElement>('.season-live__rounds button')].find(
      (b) => text(b) === label,
    );
  const roundRequests = () => http.match((req) => /\/rounds\/\d+\//.test(req.url));

  /** Answers Round `id`'s three Playback GETs, its teams named after it. */
  async function answerRound(id: number): Promise<void> {
    http.expectOne(`${BASE}/rounds/${id}/`).flush({
      data: roundDetail({ id, team_red_name: `Red ${id}`, team_blue_name: `Blue ${id}` }),
    });
    http.expectOne(`${BASE}/rounds/${id}/timeline/`).flush({ data: timeline(), meta: {} });
    http.expectOne(`${BASE}/rounds/${id}/playback/`).flush({ data: roundPlayback(), meta: {} });
    await harness.fixture.whenStable();
  }

  async function open(query: string): Promise<void> {
    await harness.navigateByUrl(`${seasonUrl('live')}${query}`);
    await releaseSeasonLayout(harness);
  }

  it('routes live to the screen, titled "One Week (Live) - <app title>"', async () => {
    await open('?rounds=31');
    await answerRound(31);
    expect(harness.fixture.debugElement.query(By.directive(SeasonLive))).not.toBeNull();
    expect(TestBed.inject(Title).getTitle()).toBe(`One Week (Live) - ${APP_TITLE}`);
  });

  it('heads the page "One Week (Live) — <League name>"', async () => {
    await open('?rounds=31');
    await answerRound(31);
    expect(text(page().querySelector('h1'))).toBe('One Week (Live) — Metro League');
  });

  it('embeds the Playback of its one Round, with no Round toggle', async () => {
    await open('?rounds=31');
    await answerRound(31);
    const playback = harness.fixture.debugElement.query(By.directive(Playback));
    expect((playback.componentInstance as Playback).roundId()).toBe(31);
    expect(text(page().querySelector('app-playback h2'))).toBe('Red 31 vs Blue 31');
    expect(page().querySelector('.season-live__rounds')).toBeNull();
  });

  it('toggles "Round 1" / "Round 2" with two Rounds, Round 1 first', async () => {
    await open('?rounds=31,32');
    await answerRound(31);
    expect([...page().querySelectorAll('.season-live__rounds button')].map((b) => text(b))).toEqual(
      ['Round 1', 'Round 2'],
    );
    expect(roundButton('Round 1')?.getAttribute('aria-pressed')).toBe('true');
    expect(roundButton('Round 2')?.getAttribute('aria-pressed')).toBe('false');

    roundButton('Round 2')?.click();
    await harness.fixture.whenStable();
    await answerRound(32);
    expect(text(page().querySelector('app-playback h2'))).toBe('Red 32 vs Blue 32');
    expect(roundButton('Round 2')?.getAttribute('aria-pressed')).toBe('true');

    roundButton('Round 1')?.click();
    await harness.fixture.whenStable();
    await answerRound(31);
    expect(text(page().querySelector('app-playback h2'))).toBe('Red 31 vs Blue 31');
  });

  it('ends with "Back to dashboard", then the note that results are final', async () => {
    await open('?rounds=31');
    await answerRound(31);
    const footer = page().querySelector('.season-live__footer');
    const back = footer?.querySelector('a');
    expect(text(back)).toBe('Back to dashboard');
    expect(back?.getAttribute('href')).toBe('/leagues/1/seasons/11');
    expect(text(footer?.lastElementChild)).toBe(FOOTER);
    expect(footer?.firstElementChild).toBe(back);
  });

  it.each(['', '?rounds=', '?rounds=abc', '?rounds=31,x', '?rounds=31,32,33'])(
    'with rounds "%s" says there is no live game, links back and loads no Round',
    async (query) => {
      await open(query);
      expect(roundRequests()).toEqual([]);
      expect(page().querySelector('app-playback')).toBeNull();
      expect(text(page())).toContain(NO_GAME);
      const back = [...page().querySelectorAll('a')].find((a) => text(a) === 'Back to dashboard');
      expect(back?.getAttribute('href')).toBe('/leagues/1/seasons/11');
      expect(text(page().querySelector('h1'))).toBe('One Week (Live) — Metro League');
    },
  );
});
