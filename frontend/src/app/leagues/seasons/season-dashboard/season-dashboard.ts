import { Component, inject } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { SEASON_MODE_LABELS, SeasonMode } from '../../../api/leagues.models';
import { SeasonsService } from '../../../api/seasons.service';
import { DashboardCards } from '../../dashboard-cards/dashboard-cards';
import { SeasonContext } from '../season-context';
import { injectSeasonLoad } from '../season-load';

/**
 * `/leagues/:id/seasons/:seasonId` (spec #444): the Season dashboard, ported from
 * `seasons/dashboard.html`. Its body cards are the shared `DashboardCards`, with no managed Team
 * (Django passes none here). No play controls (ANG-06-19).
 */
@Component({
  selector: 'app-season-dashboard',
  imports: [DashboardCards, MatAnchor, MatButton, RouterLink],
  templateUrl: './season-dashboard.html',
  styleUrl: './season-dashboard.scss',
})
export class SeasonDashboard {
  protected readonly context = inject(SeasonContext);
  private readonly seasons = inject(SeasonsService);
  private readonly load = injectSeasonLoad((id) => this.seasons.dashboard(id));
  protected readonly dash = this.load.data;
  protected readonly loadError = this.load.loadError;

  /** I2: re-sends only the dashboard GET. */
  protected retry(): void {
    this.load.retry();
  }

  protected modeLabel(mode: SeasonMode): string {
    return SEASON_MODE_LABELS[mode];
  }
}
