import { Component, inject } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { SAMPLE_FIXTURE } from '../fixture/sample-fixture';

/** Layout C Teams list: one `mat-card` per team in a responsive grid. */
@Component({
  selector: 'app-lc-teams',
  imports: [MatCardModule, MatChipsModule],
  template: `
    <h1 class="lc-h1">Teams</h1>
    <div class="lc-team-grid">
      @for (team of fixture.teams; track team.id) {
        <mat-card appearance="outlined" class="lc-team-card">
          <mat-card-header>
            <mat-card-title>{{ team.name }}</mat-card-title>
            <mat-card-subtitle>
              <span class="lc-num">{{ team.wins }}&ndash;{{ team.losses }}</span> W&ndash;L &middot;
              {{ team.players.length }} players
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>
            <mat-chip-set aria-label="Roster">
              @for (player of team.players; track player.name) {
                <mat-chip
                  >{{ player.name }}
                  <span class="lc-chip-roles">{{
                    player.preferredRoles.join(' / ')
                  }}</span></mat-chip
                >
              }
            </mat-chip-set>
          </mat-card-content>
        </mat-card>
      }
    </div>
  `,
})
export class TeamsScreenC {
  protected readonly fixture = inject(SAMPLE_FIXTURE);
}
