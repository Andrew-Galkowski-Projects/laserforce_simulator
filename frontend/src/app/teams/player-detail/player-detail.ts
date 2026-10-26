import { Component, computed, inject } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { Router, RouterLink } from '@angular/router';
import { Observable, filter, switchMap } from 'rxjs';
import { Player } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { AuthService } from '../../auth/auth.service';
import { capitalise } from '../../players/shared/benchmark-format';
import { apiMessage } from '../../shared/api-message';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import { injectNotify } from '../../shared/notify';
import { PlayerNotFound } from '../shared/player-not-found';
import { STAT_GROUPS } from '../shared/player-stats';
import { loadRoutePlayer } from '../shared/route-player';
import { loadRouteTeam } from '../shared/route-team';

/** A failed action's message when the error is not an `ApiError` (Team detail's wording). */
const FAILED = 'Something went wrong.';

/** A Stat's colour tier, as Django's `player_detail.html` bands it (80+, 60+, 40+, below 40). */
export type RatingTier = 'elite' | 'good' | 'fair' | 'poor';

function tierOf(value: number): RatingTier {
  if (value >= 80) return 'elite';
  if (value >= 60) return 'good';
  if (value >= 40) return 'fair';
  return 'poor';
}

/** One profile row: shown only when set, except Total games (always). */
interface ProfileRow {
  label: string;
  value: string;
}

/**
 * `/teams/:id/players/:playerId` (ANG-06-8a): the Player's identity card, the Self-insert button,
 * the Overall rating and the 19 Stats as tiered bars. Delete asks first, then opens the Team.
 */
@Component({
  imports: [MatAnchor, MatButton, PlayerNotFound, RouterLink],
  selector: 'app-player-detail',
  styleUrl: './player-detail.scss',
  templateUrl: './player-detail.html',
})
export class PlayerDetail {
  private readonly auth = inject(AuthService);
  private readonly players = inject(PlayersService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();
  private readonly playerLoad = loadRoutePlayer();
  private readonly teamLoad = loadRouteTeam();

  protected readonly teamId = this.playerLoad.teamId;
  protected readonly player = this.playerLoad.player;
  protected readonly team = this.teamLoad.team;
  protected readonly notFound = computed(
    () => this.playerLoad.notFound() || this.teamLoad.notFound(),
  );
  protected readonly loadError = computed(
    () => this.playerLoad.loadError() ?? this.teamLoad.loadError(),
  );

  /** The Player is the signed-in Account's Self-insert: the "You" badge and *Unlink*. */
  protected readonly isSelf = computed(() => {
    const id = this.auth.currentUser()?.self_insert?.id;
    return id !== undefined && id === this.player()?.id;
  });

  /** *This is me*: only on a Team the signed-in Account manages (the server refuses the rest). */
  protected readonly canLink = computed(() => {
    const manager = this.team()?.manager ?? null;
    return manager !== null && manager === this.auth.currentUser()?.id;
  });

  protected readonly profile = computed<ProfileRow[]>(() => {
    const p = this.player();
    if (!p) return [];
    const rows: ProfileRow[] = [];
    if (p.height) rows.push({ label: 'Height', value: p.height });
    if (p.age) rows.push({ label: 'Age', value: String(p.age) });
    if (p.started_playing_age) {
      rows.push({ label: 'Started playing', value: `age ${p.started_playing_age}` });
    }
    if (p.home_site) rows.push({ label: 'Home site', value: p.home_site });
    rows.push({ label: 'Total games', value: String(p.total_games) });
    return rows;
  });

  protected readonly roles = computed(() => (this.player()?.preferred_roles ?? []).map(capitalise));

  /** The five Stat groups with each Stat's value and tier. */
  protected readonly groups = computed(() => {
    const p = this.player();
    if (!p) return [];
    return STAT_GROUPS.map((g) => ({
      label: g.label,
      stats: g.stats.map((s) => ({ label: s.label, value: p[s.key], tier: tierOf(p[s.key]) })),
    }));
  });

  protected initial(p: Player): string {
    return p.name.charAt(0).toUpperCase();
  }

  protected overall(p: Player): string {
    return p.overall_rating.toFixed(0);
  }

  protected link(p: Player): void {
    this.run(this.auth.linkSelfInsert(p.id), `"${p.name}" is now your Self-insert.`);
  }

  protected unlink(): void {
    this.run(this.auth.unlinkSelfInsert(), 'Self-insert unlinked.');
  }

  /** Asks first; on yes deletes the Player, then a snackbar and the Team page. */
  protected remove(p: Player): void {
    const team = this.team();
    if (!team) return;
    const data: ConfirmDialogData = {
      title: 'Remove Player',
      message: `Are you sure you want to remove ${p.name} from ${team.name}? This action cannot be undone.`,
      confirm: 'Yes, Remove Player',
    };
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data })
      .afterClosed()
      .pipe(
        filter((yes) => yes === true),
        switchMap(() => this.players.delete(p.id)),
      )
      .subscribe({
        next: () => {
          this.notify(`Player "${p.name}" removed from ${team.name}`);
          void this.router.navigate(['/teams', team.id]);
        },
        error: (err: unknown) =>
          this.notify(`Could not remove ${p.name}: ${apiMessage(err, FAILED)}`),
      });
  }

  /** A Self-insert call: `done` on success, the server's message on failure. */
  private run(call: Observable<unknown>, done: string): void {
    call.subscribe({
      next: () => this.notify(done),
      error: (err: unknown) => this.notify(apiMessage(err, FAILED)),
    });
  }
}
