from django.core.management.base import BaseCommand, CommandError
from matches.models import GameRound, PlayerRoundState, GameEvent

ROLE_ORDER = ["commander", "heavy", "scout", "ammo", "medic"]


def _format_time(seconds):
    return f"{seconds // 60}:{seconds % 60:02d}"


def _uptime_breakdown(player: PlayerRoundState, events, round_duration: int = 900):
    """
    Reconstruct per-player uptime using player_downed, resupply events, and the
    is_active_at / is_taggable_at rules:
      - not_targetable:  0–3s after downed
      - reset_window:    4–7s after downed
      - active:          8s+ after downed, or never downed
      - resupplied:      tracked separately as a sub-state (player was resupplied)
    """
    # Collect downed timestamps and resupply timestamps for this player
    downed_times = sorted(
        e.timestamp
        for e in events
        if e.event_type == "player_downed" and e.target_id == player.player_id
    )
    resupply_times = sorted(
        e.timestamp
        for e in events
        if e.event_type in ("resupply_ammo", "resupply_lives")
        and e.target_id == player.player_id
    )

    end = player.was_eliminated_at if player.was_eliminated_at < 901 else round_duration

    active = 0
    not_targetable = 0
    reset_window = 0
    dead = 0

    def state_at(second, last_downed):
        if last_downed is None:
            return "active"
        delta = second - last_downed
        if delta < 4:
            return "not_targetable"
        if delta < 8:
            return "reset_window"
        return "active"

    downed_iter = iter(downed_times)
    next_downed = next(downed_iter, None)
    last_downed = None

    for second in range(0, end):
        # Advance last_downed if a downed event happened at or before this second
        while next_downed is not None and next_downed <= second:
            last_downed = next_downed
            next_downed = next(downed_iter, None)

        st = state_at(second, last_downed)
        if st == "active":
            active += 1
        elif st == "not_targetable":
            not_targetable += 1
        else:
            reset_window += 1

    if player.was_eliminated_at < 901:
        dead = round_duration - player.was_eliminated_at

    return {
        "active": active,
        "not_targetable": not_targetable,
        "reset_window": reset_window,
        "dead": dead,
        "resupply_received": len(resupply_times),
    }


class Command(BaseCommand):
    help = "Analyse a game round: reset-window tags, uptime breakdown, missile and resupply stats."

    def add_arguments(self, parser):
        parser.add_argument(
            "--round",
            dest="round_id",
            type=int,
            default=None,
            help="GameRound ID to analyse (default: most recent round)",
        )

    def handle(self, *args, **options):
        round_id = options["round_id"]
        if round_id:
            try:
                game_round = GameRound.objects.get(pk=round_id)
            except GameRound.DoesNotExist:
                raise CommandError(f"GameRound {round_id} not found.")
        else:
            game_round = GameRound.objects.order_by("-id").first()
            if not game_round:
                raise CommandError("No game rounds found in the database.")

        self.stdout.write(f"\nGame Round #{game_round.pk}  —  {game_round}\n")

        players = list(
            PlayerRoundState.objects.filter(game_round=game_round).select_related(
                "player"
            )
        )
        if not players:
            raise CommandError("No players found for this round.")

        events = list(
            GameEvent.objects.filter(game_round=game_round).select_related(
                "actor", "target"
            )
        )

        # ── Reset-window tags ──────────────────────────────────────────────────
        self.stdout.write(
            "\n--- Reset-Window Tags (tagged while 4-7s into respawn) ---\n"
        )
        self.stdout.write(
            f"{'Player':<20} {'Role':<12} {'Team':<6} {'Reset Tags':>12}\n"
        )
        self.stdout.write("-" * 54 + "\n")
        for role in ROLE_ORDER:
            for p in sorted(players, key=lambda x: x.player.name):
                if p.role != role:
                    continue
                self.stdout.write(
                    f"{p.player.name:<20} {p.role:<12} {p.team_color:<6} "
                    f"{p.times_tagged_in_reset_window:>12}\n"
                )

        # ── Uptime breakdown ───────────────────────────────────────────────────
        self.stdout.write("\n--- Uptime Breakdown (seconds over 900s round) ---\n")
        self.stdout.write(
            f"{'Player':<20} {'Role':<12} {'Active':>8} {'No-Tgt':>8} "
            f"{'Reset':>8} {'Dead':>8} {'Resups':>8}\n"
        )
        self.stdout.write("-" * 76 + "\n")
        for role in ROLE_ORDER:
            for p in sorted(players, key=lambda x: x.player.name):
                if p.role != role:
                    continue
                ut = _uptime_breakdown(p, events)
                self.stdout.write(
                    f"{p.player.name:<20} {p.role:<12} "
                    f"{ut['active']:>8} {ut['not_targetable']:>8} "
                    f"{ut['reset_window']:>8} {ut['dead']:>8} "
                    f"{ut['resupply_received']:>8}\n"
                )

        # ── Missile contribution ───────────────────────────────────────────────
        self.stdout.write("\n--- Missile Points ---\n")
        self.stdout.write(
            f"{'Player':<20} {'Role':<12} {'Missiles Hit':>14} {'Missile Pts':>13}\n"
        )
        self.stdout.write("-" * 63 + "\n")

        missile_hits = {}
        for e in events:
            if e.event_type == "missile_hit" and e.actor_id:
                missile_hits.setdefault(e.actor_id, 0)
                missile_hits[e.actor_id] += 1

        for role in ROLE_ORDER:
            for p in sorted(players, key=lambda x: x.player.name):
                if p.role != role:
                    continue
                hits = missile_hits.get(p.player_id, 0)
                pts = hits * 500
                if hits > 0:
                    self.stdout.write(
                        f"{p.player.name:<20} {p.role:<12} {hits:>14} {pts:>13}\n"
                    )

        # ── Resupply given / received ──────────────────────────────────────────
        self.stdout.write("\n--- Resupply Stats ---\n")
        self.stdout.write(
            f"{'Player':<20} {'Role':<12} {'Given':>8} {'Received':>10}\n"
        )
        self.stdout.write("-" * 54 + "\n")

        resup_received = {}
        for e in events:
            if e.event_type in ("resupply_ammo", "resupply_lives") and e.target_id:
                resup_received.setdefault(e.target_id, 0)
                resup_received[e.target_id] += 1

        for role in ROLE_ORDER:
            for p in sorted(players, key=lambda x: x.player.name):
                if p.role != role:
                    continue
                given = p.resupplies_given
                received = resup_received.get(p.player_id, 0)
                if given > 0 or received > 0:
                    self.stdout.write(
                        f"{p.player.name:<20} {p.role:<12} {given:>8} {received:>10}\n"
                    )

        self.stdout.write("\n")
