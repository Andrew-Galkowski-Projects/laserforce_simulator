import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { PlayerH2HParams, PlayerHeadToHead as PlayerH2HData } from '../../api/analytics.models';
import { AnalyticsService } from '../../api/analytics.service';
import { listAll } from '../../api/list-all';
import { Player, Role } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { H2HCard, H2HPlayerName } from '../../shared/head-to-head/h2h-card';
import { H2HCharts } from '../../shared/head-to-head/h2h-charts';
import { plural, record } from '../../shared/head-to-head/h2h-format';
import { H2HMapTable } from '../../shared/head-to-head/h2h-map-table';
import { capitalise, fixed } from '../shared/benchmark-format';
import {
  PROVENANCE_OPTIONS,
  QueryReader,
  paramOrNull,
  queryDate,
  queryId,
  queryProvenance,
  queryRole,
} from '../../shared/head-to-head/h2h-query';

/** The Role (both) select, in the Django order. */
const ROLE_OPTIONS: readonly { value: Role; label: string }[] = [
  { value: 'commander', label: 'Commander' },
  { value: 'heavy', label: 'Heavy' },
  { value: 'scout', label: 'Scout' },
  { value: 'medic', label: 'Medic' },
  { value: 'ammo', label: 'Ammo' },
];

/** The URL's picks, each dropped when the API would refuse it (INV-1). */
function playerH2HQuery(query: QueryReader): Partial<PlayerH2HParams> {
  return {
    player_a: queryId(query, 'player_a'),
    player_b: queryId(query, 'player_b'),
    role: queryRole(
      query,
      ROLE_OPTIONS.map((o) => o.value),
    ),
    provenance: queryProvenance(query),
    from: queryDate(query, 'from'),
    to: queryDate(query, 'to'),
  };
}

/** A picker option: "name (team)", or "name" for a Player with no Team name. */
function playerLabel(p: Player): string {
  return p.team_name ? `${p.name} (${p.team_name})` : p.name;
}

/**
 * `/players/head-to-head`: the **Player head-to-head record** (ANG-06-9,
 * `player_head_to_head.html`). The picks and filters live in the URL (INV-1); Compare only
 * navigates.
 */
@Component({
  selector: 'app-player-head-to-head',
  imports: [
    DatePipe,
    H2HCard,
    H2HCharts,
    H2HMapTable,
    H2HPlayerName,
    MatAnchor,
    MatButton,
    MatFormField,
    MatInput,
    MatLabel,
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './player-head-to-head.html',
  styleUrl: '../../shared/head-to-head/h2h-page.scss',
})
export class PlayerHeadToHead {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly players = signal<{ id: number; label: string }[]>([]);
  protected readonly result = signal<PlayerH2HData | null>(null);
  /** INV-2 / INV-4: the analytics call's message, shown above the picker. */
  protected readonly error = signal<string | null>(null);
  /** INV-4: the Player list's message. */
  protected readonly listError = signal<string | null>(null);
  /** INV-3: a Player is missing or another Account's. */
  protected readonly notFound = signal(false);
  protected readonly provenanceOptions = PROVENANCE_OPTIONS;
  protected readonly roleOptions = ROLE_OPTIONS;
  protected readonly plural = plural;
  protected readonly record = record;
  protected readonly fixed = fixed;
  protected readonly capitalise = capitalise;

  protected readonly form = inject(NonNullableFormBuilder).group({
    player_a: '',
    player_b: '',
    role: '',
    provenance: 'all',
    from: '',
    to: '',
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    const players = inject(PlayersService);
    const analytics = inject(AnalyticsService);
    listAll((page) => players.list({ ...page }))
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe({
        next: (all) => this.players.set(all.map((p) => ({ id: p.id, label: playerLabel(p) }))),
        error: (error: unknown) => this.listError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });

    this.route.queryParamMap
      .pipe(
        switchMap((query) => {
          const q = playerH2HQuery(query);
          this.form.setValue({
            player_a: q.player_a?.toString() ?? '',
            player_b: q.player_b?.toString() ?? '',
            role: q.role ?? '',
            provenance: q.provenance ?? 'all',
            from: q.from ?? '',
            to: q.to ?? '',
          });
          this.result.set(null);
          this.error.set(null);
          this.notFound.set(false);
          if (q.player_a === undefined || q.player_b === undefined) return EMPTY;
          const params = { ...q, player_a: q.player_a, player_b: q.player_b };
          return analytics.playerHeadToHead(params).pipe(
            catchError((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) this.notFound.set(true);
              else this.error.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((loaded) => this.result.set(loaded));
  }

  /** Writes the picks to the URL, which loads the record. */
  protected compare(): void {
    const v = this.form.getRawValue();
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        player_a: paramOrNull(v.player_a),
        player_b: paramOrNull(v.player_b),
        role: paramOrNull(v.role),
        provenance: v.provenance === 'all' ? null : v.provenance,
        from: paramOrNull(v.from),
        to: paramOrNull(v.to),
      },
    });
  }
}
