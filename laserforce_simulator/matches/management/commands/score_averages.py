import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from django.core.management.base import BaseCommand, CommandError

from matches.sim_helpers.parallel_worker import score_round_worker, worker_django_init
from matches.sim_helpers.time_constants import (
    SURVIVED_SENTINEL,
    TICK_SECONDS,
    TICKS_PER_ROUND,
)
from matches.simulation import BatchSimulator, _precompute_roster
from teams.models import Team

ROLE_ORDER = ["commander", "heavy", "scout", "ammo", "medic"]
TARGETS = {
    "commander": 9952,
    "heavy": 6482,
    "scout": 5102,
    "ammo": 3242,
    "medic": 2282,
}


class Command(BaseCommand):
    help = "Batch-simulate rounds and print average scores per role."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rounds",
            type=int,
            default=50,
            help="Number of rounds to simulate (default: 50)",
        )
        parser.add_argument(
            "--team-red",
            dest="team_red",
            default=None,
            help="Name of the red team (default: first team in DB)",
        )
        parser.add_argument(
            "--team-blue",
            dest="team_blue",
            default=None,
            help="Name of the blue team (default: second team in DB)",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Optional RNG seed for reproducible results",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="Number of parallel worker processes (default: 1 = serial)",
        )

    def _team_with_roster(self, exclude_pk):
        qs = Team.objects.all()
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        for team in qs:
            if list(team.active_roster):
                return team
        return None

    def handle(self, *args, **options):
        n = options["rounds"]
        workers = options.get("workers", 1) or 1

        if options["team_red"]:
            try:
                team_red = Team.objects.get(name=options["team_red"])
            except Team.DoesNotExist:
                raise CommandError(f"Team '{options['team_red']}' not found.")
        else:
            team_red = self._team_with_roster(None)
            if not team_red:
                raise CommandError("No teams with active rosters found.")

        if options["team_blue"]:
            try:
                team_blue = Team.objects.get(name=options["team_blue"])
            except Team.DoesNotExist:
                raise CommandError(f"Team '{options['team_blue']}' not found.")
        else:
            team_blue = self._team_with_roster(team_red.pk)
            if not team_blue:
                team_blue = team_red  # mirror match if only one valid team

        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)

        if not red_roster:
            raise CommandError(f"Team '{team_red.name}' has no active roster.")
        if not blue_roster:
            raise CommandError(f"Team '{team_blue.name}' has no active roster.")

        if options["seed"] is not None:
            random.seed(options["seed"])

        parallel_note = f" ({workers} workers)" if workers > 1 else ""
        self.stdout.write(
            f"\nSimulating {n} rounds{parallel_note}: "
            f"{team_red.name} (red) vs {team_blue.name} (blue)\n"
        )

        role_scores = defaultdict(list)
        role_tags = defaultdict(list)
        role_tagged = defaultdict(list)
        role_missile_pts = defaultdict(list)
        role_reset_window_tags = defaultdict(list)
        role_ticks_active = defaultdict(list)
        role_ticks_not_targetable = defaultdict(list)
        role_ticks_reset_window = defaultdict(list)
        role_ticks_dead = defaultdict(list)
        role_follow_up_shots = defaultdict(list)
        role_reaction_shots = defaultdict(list)

        if workers > 1:
            red_data = _precompute_roster(red_roster)
            blue_data = _precompute_roster(blue_roster)

            seed_states = []
            for _ in range(n):
                seed_states.append(random.getstate())
                random.random()

            args_list = [(red_data, blue_data, s) for s in seed_states]
            chunksize = max(1, n // (workers * 4))

            with ProcessPoolExecutor(
                max_workers=workers, initializer=worker_django_init
            ) as executor:
                all_player_stats = list(
                    executor.map(score_round_worker, args_list, chunksize=chunksize)
                )

            for player_stats_list in all_player_stats:
                for p in player_stats_list:
                    role = p["role"]
                    role_scores[role].append(p["points_scored"])
                    role_tags[role].append(p["tags_made"])
                    role_tagged[role].append(p["times_tagged"])
                    role_missile_pts[role].append(p["missile_points"])
                    role_reset_window_tags[role].append(
                        p["times_tagged_in_reset_window"]
                    )
                    # TIME-01: workers return tick-valued uptime; aggregate in
                    # ticks here, divide by 2 at the display boundary below.
                    role_ticks_active[role].append(p["ticks_active"])
                    role_ticks_not_targetable[role].append(p["ticks_not_targetable"])
                    role_ticks_reset_window[role].append(p["ticks_reset_window"])
                    dead = (
                        TICKS_PER_ROUND - p["was_eliminated_at"]
                        if p["was_eliminated_at"] < SURVIVED_SENTINEL
                        else 0
                    )
                    role_ticks_dead[role].append(dead)
                    role_follow_up_shots[role].append(p["follow_up_shots"])
                    role_reaction_shots[role].append(p["reaction_shots"])
        else:
            sim = BatchSimulator()
            for _ in range(n):
                _, red_players, blue_players = sim._simulate_round(
                    red_roster, blue_roster
                )
                for p in red_players + blue_players:
                    role_scores[p.role].append(p.points_scored)
                    role_tags[p.role].append(p.tags_made)
                    role_tagged[p.role].append(p.times_tagged)
                    role_missile_pts[p.role].append(p.missile_points)
                    role_reset_window_tags[p.role].append(
                        p.times_tagged_in_reset_window
                    )
                    # TIME-01: PlayerState now accumulates tick-valued uptime;
                    # aggregate in ticks, divide by 2 at the display boundary.
                    role_ticks_active[p.role].append(p.ticks_active)
                    role_ticks_not_targetable[p.role].append(p.ticks_not_targetable)
                    role_ticks_reset_window[p.role].append(p.ticks_reset_window)
                    dead = (
                        TICKS_PER_ROUND - p.was_eliminated_at
                        if p.was_eliminated_at < SURVIVED_SENTINEL
                        else 0
                    )
                    role_ticks_dead[p.role].append(dead)
                    role_follow_up_shots[p.role].append(p.follow_up_shots)
                    role_reaction_shots[p.role].append(p.reaction_shots)

        # ── Score summary ────────────────────────────────────────────────────
        self.stdout.write(
            f"\n{'Role':<12} {'Avg Score':>10} {'Target':>10} {'Diff':>8} "
            f"{'Avg Tags':>10} {'Avg Tagged':>12}\n"
        )
        self.stdout.write("-" * 66 + "\n")

        for role in ROLE_ORDER:
            scores = role_scores.get(role, [])
            if not scores:
                continue
            avg = sum(scores) / len(scores)
            avg_tags = sum(role_tags[role]) / len(role_tags[role])
            avg_tagged = sum(role_tagged[role]) / len(role_tagged[role])
            target = TARGETS.get(role, 0)
            diff = avg - target
            diff_str = f"{diff:+.0f}"
            style = (
                self.style.ERROR
                if diff > 1000
                else (self.style.WARNING if diff > 500 else self.style.SUCCESS)
            )
            self.stdout.write(
                style(
                    f"{role:<12} {avg:>10.0f} {target:>10} {diff_str:>8} "
                    f"{avg_tags:>10.1f} {avg_tagged:>12.1f}"
                )
                + "\n"
            )

        # ── Missile breakdown ────────────────────────────────────────────────
        self.stdout.write(
            f"\n{'Role':<12} {'Avg Msl Pts':>12} {'% of Score':>12} {'Avg Msls Hit':>14}\n"
        )
        self.stdout.write("-" * 54 + "\n")

        for role in ROLE_ORDER:
            scores = role_scores.get(role, [])
            msl = role_missile_pts.get(role, [])
            if not scores:
                continue
            avg_score = sum(scores) / len(scores)
            avg_msl = sum(msl) / len(msl) if msl else 0
            avg_hits = avg_msl / 500 if avg_msl else 0
            pct = (avg_msl / avg_score * 100) if avg_score else 0
            self.stdout.write(
                f"{role:<12} {avg_msl:>12.0f} {pct:>11.1f}% {avg_hits:>14.1f}\n"
            )

        # ── Reset-window tags ────────────────────────────────────────────────
        self.stdout.write(f"\n{'Role':<12} {'Avg Reset Tags':>16}\n")
        self.stdout.write("-" * 30 + "\n")

        for role in ROLE_ORDER:
            rw = role_reset_window_tags.get(role, [])
            if not rw:
                continue
            avg_rw = sum(rw) / len(rw)
            self.stdout.write(f"{role:<12} {avg_rw:>16.2f}\n")

        # ── Uptime breakdown ─────────────────────────────────────────────────
        self.stdout.write(
            f"\n{'Role':<12} {'Active-time':>12} {'Reset-time':>12} {'Dead-time':>12} {'No-Tgt-time':>8}  "
            f"{'Active%':>8} {'Reset%':>8} {'Dead%':>8} {'No-Tgt%':>8}\n"
        )
        self.stdout.write("-" * 88 + "\n")

        for role in ROLE_ORDER:
            sa = role_ticks_active.get(role, [])
            sn = role_ticks_not_targetable.get(role, [])
            sr = role_ticks_reset_window.get(role, [])
            sd = role_ticks_dead.get(role, [])
            if not sa:
                continue
            avg_a = sum(sa) / len(sa)
            avg_n = sum(sn) / len(sn)
            avg_r = sum(sr) / len(sr)
            avg_d = sum(sd) / len(sd)
            total = avg_a + avg_n + avg_r + avg_d or 1
            # TIME-01 DISPLAY boundary: averages are tick-valued; show seconds
            # (÷2). Percentages are domain-invariant ratios, computed on ticks.
            self.stdout.write(
                f"{role:<12} {avg_a * TICK_SECONDS:>12.0f} {avg_r * TICK_SECONDS:>12.0f} "
                f"{avg_d * TICK_SECONDS:>12.0f} {avg_n * TICK_SECONDS:>8.0f}  "
                f"{avg_a/total*100:>7.1f}% {avg_r/total*100:>7.1f}% "
                f"{avg_d/total*100:>7.1f}% {avg_n/total*100:>7.1f}%\n"
            )

        # ── Follow-up & reaction shots ───────────────────────────────────────
        self.stdout.write(
            f"\n{'Role':<12} {'Avg FU Shots':>14} {'FU% of Tags':>12} "
            f"{'Avg React Shots':>16} {'React% of Tags':>16}\n"
        )
        self.stdout.write("-" * 74 + "\n")

        for role in ROLE_ORDER:
            fu = role_follow_up_shots.get(role, [])
            rx = role_reaction_shots.get(role, [])
            tags = role_tags.get(role, [])
            if not fu:
                continue
            avg_fu = sum(fu) / len(fu)
            avg_rx = sum(rx) / len(rx)
            avg_tags = sum(tags) / len(tags) if tags else 1
            fu_pct = avg_fu / avg_tags * 100 if avg_tags else 0
            rx_pct = avg_rx / avg_tags * 100 if avg_tags else 0
            self.stdout.write(
                f"{role:<12} {avg_fu:>14.2f} {fu_pct:>11.1f}% "
                f"{avg_rx:>16.2f} {rx_pct:>15.1f}%\n"
            )

        self.stdout.write("\n")
