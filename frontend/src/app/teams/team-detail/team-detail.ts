import { Component, computed, inject } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { RouterLink } from '@angular/router';
import { filter, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Player, Role } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { AuthService } from '../../auth/auth.service';
import { injectNotify } from '../../shared/notify';
import { ROLE_LABELS, SLOTS, benchPlayers } from '../shared/roster';
import { loadRouteTeam } from '../shared/route-team';
import { TeamNotFound } from '../shared/team-not-found';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';

/**
 * One Active Slots row: the slot's label, the slotted Player id and that Player, or `null`
 * when the id is not on this Team (the API sends only this Team's players).
 */
interface SlotRow {
  label: string;
  id: number;
  player: Player | null;
}

/** `/teams/:id`: the Team's roster (active slots and bench), its validity and Remove player. */
@Component({
  imports: [MatAnchor, MatButton, RouterLink, TeamNotFound],
  selector: 'app-team-detail',
  styleUrl: './team-detail.scss',
  templateUrl: './team-detail.html',
})
export class TeamDetail {
  private readonly auth = inject(AuthService);
  private readonly players = inject(PlayersService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = injectNotify();
  private readonly load = loadRouteTeam();

  /** The signed-in Account's self-insert Player id (the "You" badge), or null. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);
  protected readonly roleLabels = ROLE_LABELS;
  /** The Roster Status rows: each role's label and its slot count (Scout has two). */
  protected readonly roles = (Object.keys(ROLE_LABELS) as Role[]).map((r) => ({
    label: ROLE_LABELS[r],
    slots: r === 'scout' ? '2 slots' : '1 slot',
  }));
  protected readonly team = this.load.team;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;

  /**
   * The filled slots in slot order, as Django's `active_roster` lists them: a slot naming a
   * Player not on this Team is still a row (with `player: null`); only an empty slot is skipped.
   */
  protected readonly active = computed<SlotRow[]>(() => {
    const t = this.team();
    if (!t) return [];
    const byId = new Map(t.players.map((p) => [p.id, p]));
    return SLOTS.flatMap((s) => {
      const id = t[s.key];
      return id === null ? [] : [{ label: s.label, id, player: byId.get(id) ?? null }];
    });
  });

  protected readonly bench = computed<Player[]>(() => {
    const t = this.team();
    return t ? benchPlayers(t, t.players) : [];
  });

  /** Asks first; on yes deletes the Player (hard delete), reloads the Team, then a snackbar. */
  protected remove(player: Player): void {
    const team = this.team();
    if (!team) return;
    const data: ConfirmDialogData = {
      title: 'Remove player',
      message: `Remove ${player.name} from ${team.name}? This deletes the player.`,
      confirm: 'Remove',
    };
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data })
      .afterClosed()
      .pipe(
        filter((yes) => yes === true),
        switchMap(() => this.players.delete(player.id)),
      )
      .subscribe({
        next: () =>
          this.load.reload(() => this.notify(`Player "${player.name}" removed from ${team.name}`)),
        error: (err: unknown) => {
          const reason = err instanceof ApiError ? err.message : 'Something went wrong.';
          this.notify(`Could not remove ${player.name}: ${reason}`);
        },
      });
  }
}
