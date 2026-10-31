import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By, Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { LeagueLayout } from '../league-layout/league-layout';
import { ManageConferences } from './manage-conferences/manage-conferences';
import { OwnerEvaluation } from './owner-evaluation/owner-evaluation';
import { SeasonAwards } from './season-awards/season-awards';
import { SeasonDashboard } from './season-dashboard/season-dashboard';
import { SeasonLayout } from './season-layout/season-layout';
import { SeasonLive } from './season-live/season-live';
import { SeasonSchedule } from './season-schedule/season-schedule';
import { SeasonStandings } from './season-standings/season-standings';
import { seasonUrl } from './testing/season-fixtures';
import { releaseSeasonLayout } from './testing/season-loads';

describe('Season routes (/leagues/:id/seasons/:seasonId/..., spec #444)', () => {
  let http: HttpTestingController;

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
  });

  // Routing only: drop every load, so the screen tickets never edit this file.
  afterEach(() => http.match(() => true));

  const routerUrl = (): string => TestBed.inject(Router).url;

  it.each([
    ['', SeasonDashboard, 'Season - Laserforce Simulator'],
    ['standings', SeasonStandings, 'Standings - Laserforce Simulator'],
    ['schedule', SeasonSchedule, 'Schedule - Laserforce Simulator'],
    ['awards', SeasonAwards, 'Season Awards - Laserforce Simulator'],
    ['owner-evaluation', OwnerEvaluation, 'Owner Evaluation - Laserforce Simulator'],
    ['conferences', ManageConferences, 'Manage Conferences - Laserforce Simulator'],
    ['live', SeasonLive, 'One Week (Live) - Laserforce Simulator'],
  ])(
    'resolves seasons/11/%s to its screen inside both layouts, with its page title',
    async (path, screen, title) => {
      const harness = await RouterTestingHarness.create();
      const url = seasonUrl(path);
      await harness.navigateByUrl(url);
      await releaseSeasonLayout(harness);
      expect(routerUrl()).toBe(url);
      const root = harness.fixture.debugElement;
      expect(root.query(By.directive(screen))).not.toBeNull();
      expect(root.query(By.directive(SeasonLayout))).not.toBeNull();
      expect(root.query(By.directive(LeagueLayout))).not.toBeNull();
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    },
  );
});
