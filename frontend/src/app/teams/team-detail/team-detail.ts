import { Component, computed, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { filter, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Player, Role } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { Team } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { AuthService } from '../../auth/auth.service';
import { ROLE_LABELS, SLOTS, benchPlayers } from '../shared/roster';
import { TeamNotFound } from '../shared/team-not-found';
import { ConfirmDialog, ConfirmDialogData } from './confirm-dialog';

/** One Active Slots row: the slot's label and the Player in it. */
interface SlotRow {
  label: string;
  player: Player;
}

/** `/teams/:id`: the Team's roster (active slots and bench), its validity and Remove player. */
@Component({
  imports: [MatAnchor, MatButton, RouterLink, TeamNotFound],
  selector: 'app-team-detail',
  styleUrl: './team-detail.scss',
  templateUrl: './team-detail.html',
})
export class TeamDetail {
  private readonly teams = inject(TeamsService);
  private readonly auth = inject(AuthService);
  private readonly players = inject(PlayersService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly id = Number(inject(ActivatedRoute).snapshot.paramMap.get('id'));

  /** The signed-in Account's self-insert Player id (the "You" badge), or null. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);
  protected readonly roleLabels = ROLE_LABELS;
  /** The Roster Status rows: each role's label and its slot count (Scout has two). */
  protected readonly roles = (Object.keys(ROLE_LABELS) as Role[]).map((r) => ({
    label: ROLE_LABELS[r],
    slots: r === 'scout' ? '2 slots' : '1 slot',
  }));
  protected readonly team = signal<Team | null>(null);
  /** True once `GET teams/{id}/` answered 404 (a missing Team, or another Account's). */
  protected readonly notFound = signal(false);
  /** Any other load failure, shown as an alert. */
  protected readonly loadError = signal<string | null>(null);

  /** The filled slots in slot order (an empty slot, or one naming an unknown Player, is no row). */
  protected readonly active = computed<SlotRow[]>(() => {
    const t = this.team();
    if (!t) return [];
    const byId = new Map(t.players.map((p) => [p.id, p]));
    return SLOTS.flatMap((s) => {
      const id = t[s.key];
      const p = id === null ? undefined : byId.get(id);
      return p ? [{ label: s.label, player: p }] : [];
    });
  });

  protected readonly bench = computed<Player[]>(() => {
    const t = this.team();
    return t ? benchPlayers(t, t.players) : [];
  });

  constructor() {
    this.load();
  }

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
          this.load(() => this.notify(`Player "${player.name}" removed from ${team.name}`)),
        error: (err: unknown) =>
          this.notify(`Could not remove ${player.name}: ${(err as Error).message}`),
      });
  }

  private notify(message: string): void {
    this.snackBar.open(message, 'Dismiss', { duration: 5000 });
  }

  /** `GET teams/{id}/`, then `done` once the Team is shown. */
  private load(done?: () => void): void {
    this.teams.get(this.id).subscribe({
      next: (t) => {
        this.team.set(t);
        done?.();
      },
      error: (err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          this.notFound.set(true);
        } else {
          this.loadError.set(`Could not load the Team. ${(err as Error).message}`);
        }
      },
    });
  }
}
