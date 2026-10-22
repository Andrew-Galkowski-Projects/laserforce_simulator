import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, tap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Player, PlayerInput } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { injectNotify } from '../shared/notify';
import { PlayerForm } from '../shared/player-form';

/**
 * `/teams/:id/players/:playerId/edit`: edit a Player, then land on its Team's detail page.
 * A 404 (a missing or another Account's Player) or a Player on another Team is "Player not found".
 */
@Component({
  selector: 'app-player-edit',
  imports: [PlayerForm, RouterLink],
  styleUrl: '../shared/team-page.scss',
  template: `
    @if (notFound()) {
      <h1>Player not found</h1>
      <p><a [routerLink]="['/teams', teamId()]">&larr; Back to Team</a></p>
    } @else if (loadError(); as message) {
      <p role="alert">{{ message }}</p>
    } @else if (player(); as player) {
      <h1>Edit {{ player.name }}</h1>
      <app-player-form
        submitLabel="Save Changes"
        [cancelLink]="'/teams/' + teamId()"
        [initial]="player"
        [busy]="busy()"
        [error]="error()"
        (submitted)="save(player, $event)"
      />
    }
  `,
})
export class PlayerEdit {
  private readonly players = inject(PlayersService);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();

  /** The route's `:id`: the Team the Player must belong to. */
  protected readonly teamId = signal(0);
  protected readonly player = signal<Player | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(
        switchMap((params) => {
          const teamId = Number(params.get('id'));
          this.teamId.set(teamId);
          return this.players.get(Number(params.get('playerId'))).pipe(
            tap((player) => {
              if (player.team === teamId) {
                this.player.set(player);
              } else {
                this.notFound.set(true);
              }
            }),
            catchError((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) {
                this.notFound.set(true);
              } else {
                this.loadError.set(
                  error instanceof ApiError
                    ? `Could not load the Player. ${error.message}`
                    : 'Could not load the Player.',
                );
              }
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe();
  }

  protected save(player: Player, input: PlayerInput): void {
    this.busy.set(true);
    this.players.update(player.id, input).subscribe({
      next: (saved) => {
        this.notify(`Player "${saved.name}" updated!`);
        void this.router.navigate(['/teams', this.teamId()]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(error);
      },
    });
  }
}
