import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable, Subject, combineLatest, map, startWith, switchMap, tap } from 'rxjs';
import { LeaguePlayer, LeaguePlayerStatRow } from '../../api/league-players.models';
import { LeaguePlayersService } from '../../api/league-players.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { ChartTheme, LineChart, LineSeries } from '../../shared/line-chart';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { injectNotify } from '../../shared/notify';
import { readToken } from '../../shared/read-token';
import { SoonBadge } from '../soon-badge/soon-badge';
import { LeagueContext } from '../stats-shared/league-context';
import {
  RATING_STAT_KEYS,
  STAT_COLUMNS,
  orDash,
  potentialText,
  roleTitle,
  statCell,
} from '../stats-shared/player-columns';
import { WatchFlag } from '../watch/watch-flag';

/** The chart's series label and y-axis title (Django's chart). */
const OVERALL_LABEL = 'Overall rating';

/** The 19 current ratings in `player_detail.html`'s five groups, with its labels. */
const RATING_GROUPS: readonly { label: string; stats: readonly [string, string][] }[] = [
  {
    label: 'Awareness',
    stats: [
      ['player_awareness', 'Player Awareness'],
      ['game_awareness', 'Game Awareness'],
      ['resource_awareness', 'Resource Awareness'],
    ],
  },
  { label: 'Decision-making', stats: [['decision_making', 'Decision Making']] },
  {
    label: 'Physical',
    stats: [
      ['positioning', 'Positioning'],
      ['stamina', 'Stamina'],
      ['speed', 'Speed'],
      ['flexibility', 'Flexibility'],
      ['adaptability', 'Adaptability'],
    ],
  },
  {
    label: 'Team',
    stats: [
      ['communication', 'Communication'],
      ['teamwork', 'Teamwork'],
    ],
  },
  {
    label: 'Role',
    stats: [
      ['offensive_synergy', 'Offensive Synergy'],
      ['defensive_synergy', 'Defensive Synergy'],
      ['midfield_synergy', 'Midfield Synergy'],
      ['resupply_synergy', 'Resupply Synergy'],
      ['resupply_efficiency', 'Resupply Efficiency'],
      ['accuracy', 'Accuracy'],
      ['survival', 'Survival'],
      ['special_usage', 'Special Usage'],
    ],
  },
];

/** The Ratings history table's 19 rating headers, in `RATING_STAT_KEYS` order. */
const HISTORY_HEADERS: readonly string[] = [
  'Player Aware',
  'Game Aware',
  'Resource Aware',
  'Decision',
  'Positioning',
  'Stamina',
  'Speed',
  'Flexibility',
  'Adaptability',
  'Communication',
  'Teamwork',
  'Offensive Syn',
  'Defensive Syn',
  'Midfield Syn',
  'Resupply Syn',
  'Resupply Eff',
  'Accuracy',
  'Survival',
  'Special Usage',
];

/** One Regular Season / Career row as shown. */
interface RsRow {
  key: string;
  year: string;
  team: string;
  games: number;
  cells: string[];
  career: boolean;
}

/**
 * `/leagues/:id/players/:playerId` (spec #428 Player detail, `leagues/player_detail.html`): the
 * League player page. The header (★, name, You badge, Career stats (global), This is me /
 * Unlink), the Overall and Potential cards, the 19 ratings, the Regular Season table, the
 * Coming-soon cards, the Ratings history chart + table and the Awards.
 */
@Component({
  selector: 'app-league-player',
  imports: [LineChart, MatAnchor, MatButton, RouterLink, SoonBadge, WatchFlag],
  templateUrl: './league-player.html',
  styleUrl: './league-player.scss',
})
export class LeaguePlayerDetail {
  private readonly players = inject(LeaguePlayersService);
  private readonly auth = inject(AuthService);
  private readonly notify = injectNotify();
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;
  protected readonly leagueId = this.context.leagueId;
  private readonly route = inject(ActivatedRoute);

  protected readonly data = signal<LeaguePlayer | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  private readonly reloads = new Subject<void>();

  /** The Regular Season stat headers: `STAT_COLUMNS`' (Django's "Survival"; the cells keep `s`). */
  protected readonly rsHeaders = STAT_COLUMNS.map((c) => c.label);
  protected readonly historyHeaders = HISTORY_HEADERS;
  protected readonly overallLabel = OVERALL_LABEL;

  /** The Player is the signed-in Account's Self-insert: the "You" badge and *Unlink*. */
  protected readonly isSelf = computed(() => {
    const id = this.auth.currentUser()?.self_insert?.id;
    return id !== undefined && id === this.data()?.player.id;
  });

  /**
   * *This is me*: the server says the Account may link this Player. The template offers it only
   * when `isSelf()` is false (Unlink takes its place otherwise).
   */
  protected readonly canLink = computed(() => this.data()?.can_link_self_insert ?? false);

  protected readonly roles = computed(() =>
    (this.data()?.bio.preferred_roles ?? []).map(roleTitle),
  );

  protected readonly groups = computed(() => {
    const stats = this.data()?.stats ?? {};
    return RATING_GROUPS.map((g) => ({
      label: g.label,
      stats: g.stats.map(([key, label]) => ({ label, value: stats[key] })),
    }));
  });

  /** One row per Season, then the Career row (when any). */
  protected readonly rsRows = computed<RsRow[]>(() => {
    const d = this.data();
    if (!d) return [];
    const rows = d.seasons.map((s) => rsRow(`season-${s.season.id}`, s.season.name, s, false));
    if (d.career) rows.push(rsRow('career', 'Career', d.career, true));
    return rows;
  });

  protected readonly historyRows = computed(() =>
    (this.data()?.ratings_history ?? []).map((r) => ({
      key: r.season.id,
      season: r.season.name,
      age: orDash(r.age),
      overall: r.overall_rating.toFixed(1),
      potential: potentialText(r.potential),
      ratings: RATING_STAT_KEYS.map((k) => r.stats[k]),
    })),
  );

  /** The chart chrome from the tokens, read when the Player loads (ADR-0045). */
  protected readonly theme = computed<ChartTheme>(() => {
    this.data();
    const muted = readToken('--lf-text-muted');
    return {
      text: muted,
      grid: readToken('--lf-border'),
      cap: muted,
      font: readToken('--lf-font-body'),
    };
  });

  /** The chart's name for Season number `x` (1-based): that snapshot's Season name. */
  protected readonly seasonLabel = (x: number): string =>
    this.data()?.ratings_history[x - 1]?.season.name ?? String(x);

  /** Overall rating by Season number (1 = the oldest snapshot). */
  protected readonly trend = computed<LineSeries[]>(() => [
    {
      label: OVERALL_LABEL,
      color: readToken('--lf-accent'),
      pointRadius: 2,
      points: (this.data()?.ratings_history ?? []).map((r, i) => ({
        sec: i + 1,
        value: r.overall_rating,
      })),
    },
  ]);

  constructor() {
    const leagueParams = (this.route.parent ?? this.route).paramMap;
    combineLatest([
      leagueParams.pipe(map((p) => Number(p.get('id')))),
      this.route.paramMap.pipe(map((p) => Number(p.get('playerId')))),
      this.reloads.pipe(startWith(undefined)),
    ])
      .pipe(
        tap(() => {
          this.data.set(null);
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([id, playerId]) =>
          this.players
            .player(id, playerId)
            .pipe(notFoundOrError<LeaguePlayer>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.data.set(loaded));
  }

  /** I2: re-sends the same GET. */
  protected retry(): void {
    this.reloads.next();
  }

  protected readonly potentialText = potentialText;

  protected overall(d: LeaguePlayer): string {
    return d.overall_rating.toFixed(0);
  }

  protected link(d: LeaguePlayer): void {
    this.run(this.auth.linkSelfInsert(d.player.id), `"${d.player.name}" is now your Self-insert.`);
  }

  protected unlink(): void {
    this.run(this.auth.unlinkSelfInsert(), 'Self-insert unlinked.');
  }

  /** A Self-insert call: `done` on success, the server's message on failure. */
  private run(call: Observable<unknown>, done: string): void {
    call.subscribe({
      next: () => this.notify(done),
      error: (err: unknown) => this.notify(apiMessage(err, GENERIC_ERROR_MESSAGE)),
    });
  }
}

function rsRow(key: string, year: string, row: LeaguePlayerStatRow, career: boolean): RsRow {
  return {
    key,
    year,
    team: orDash(row.team?.name ?? null),
    games: row.games,
    cells: STAT_COLUMNS.map((c) => statCell(row.stats, c)),
    career,
  };
}
