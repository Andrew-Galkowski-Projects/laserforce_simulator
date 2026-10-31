import { Routes } from '@angular/router';
import { APP_TITLE } from '../../app-title';
import { KEEP_ON_PLAY_CHANGE } from '../play/play-run';
import { ManageConferences } from './manage-conferences/manage-conferences';
import { OwnerEvaluation } from './owner-evaluation/owner-evaluation';
import { SeasonAwards } from './season-awards/season-awards';
import { SeasonDashboard } from './season-dashboard/season-dashboard';
import { SeasonLayout } from './season-layout/season-layout';
import { SeasonLive } from './season-live/season-live';
import { SeasonSchedule } from './season-schedule/season-schedule';
import { SeasonStandings } from './season-standings/season-standings';

/**
 * The Season screens (spec #444), lazily loaded under `/leagues/:id/seasons/:seasonId` by
 * `LEAGUES_ROUTES`, inside the League layout. `SeasonLayout` loads the Season and renders the
 * routed screen once it has; each screen lives in its own folder.
 */
export const SEASON_ROUTES: Routes = [
  {
    path: '',
    component: SeasonLayout,
    children: [
      { path: '', component: SeasonDashboard, title: `Season - ${APP_TITLE}` },
      { path: 'standings', component: SeasonStandings, title: `Standings - ${APP_TITLE}` },
      { path: 'schedule', component: SeasonSchedule, title: `Schedule - ${APP_TITLE}` },
      { path: 'awards', component: SeasonAwards, title: `Season Awards - ${APP_TITLE}` },
      {
        path: 'owner-evaluation',
        component: OwnerEvaluation,
        title: `Owner Evaluation - ${APP_TITLE}`,
      },
      {
        path: 'conferences',
        component: ManageConferences,
        title: `Manage Conferences - ${APP_TITLE}`,
      },
      // Spec #454: `?rounds=<id>[,<id>]`, the Round(s) a Live play action just played. Its
      // results are final, so a finished play run does not re-create it.
      {
        path: 'live',
        component: SeasonLive,
        title: `One Week (Live) - ${APP_TITLE}`,
        data: KEEP_ON_PLAY_CHANGE,
      },
    ],
  },
];
