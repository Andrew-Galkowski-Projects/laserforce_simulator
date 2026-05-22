from datetime import datetime
from django.db import models
from teams.models import Team, Player
from matches.sim_helpers.role_constants import MAX_LIVES, MAX_SHOTS, ROLE_STATS
from matches.sim_helpers.time_constants import (
    SURVIVED_SENTINEL,
    TICK_SECONDS,
    TICKS_PER_ROUND,
)


class Match(models.Model):
    MATCH_TYPES = [
        ("tournament", "Tournament Match"),
        ("friendly", "Friendly Match"),
    ]

    team_red = models.ForeignKey(
        Team, related_name="red_matches", on_delete=models.CASCADE
    )
    team_blue = models.ForeignKey(
        Team, related_name="blue_matches", on_delete=models.CASCADE
    )
    match_type = models.CharField(
        max_length=20, choices=MATCH_TYPES, default="friendly"
    )
    date_played = models.DateTimeField(auto_now_add=True)

    # Round 1 scores (team_red starts as red)
    red_round1_points = models.IntegerField(default=0)
    blue_round1_points = models.IntegerField(default=0)
    red_round1_eliminated = models.BooleanField(default=False)
    blue_round1_eliminated = models.BooleanField(default=False)
    round1_eliminated_at = models.IntegerField(default=SURVIVED_SENTINEL)

    # Round 2 scores (teams switch colors)
    red_round2_points = models.IntegerField(default=0)
    blue_round2_points = models.IntegerField(default=0)
    red_round2_eliminated = models.BooleanField(default=False)
    blue_round2_eliminated = models.BooleanField(default=False)
    round2_eliminated_at = models.IntegerField(default=SURVIVED_SENTINEL)

    # Bonus points for eliminations
    red_bonus_points = models.IntegerField(default=0)
    blue_bonus_points = models.IntegerField(default=0)

    # Match result
    winner = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        related_name="won_matches",
        on_delete=models.SET_NULL,
    )
    is_completed = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Matches"

    def __str__(self):
        return f"{self.team_red.name} vs {self.team_blue.name} ({self.get_match_type_display()})"

    @property
    def red_total_points(self):
        return self.red_round1_points + self.red_round2_points + self.red_bonus_points

    @property
    def blue_total_points(self):
        return (
            self.blue_round1_points + self.blue_round2_points + self.blue_bonus_points
        )

    @property
    def red_rounds_won(self):
        rounds = 0
        if self.red_round1_points > self.blue_round1_points:
            rounds += 1
        if self.red_round2_points > self.blue_round2_points:
            rounds += 1
        return rounds

    @property
    def blue_rounds_won(self):
        rounds = 0
        if self.blue_round1_points > self.red_round1_points:
            rounds += 1
        if self.blue_round2_points > self.red_round2_points:
            rounds += 1
        return rounds

    def calculate_winner(self):
        """Calculate match winner based on rounds won and total points"""
        red_rounds = self.red_rounds_won
        blue_rounds = self.blue_rounds_won

        # Winner determined by rounds won first
        if red_rounds > blue_rounds:
            return self.team_red
        elif blue_rounds > red_rounds:
            return self.team_blue
        else:
            # Tied on rounds, use total points
            if self.red_total_points > self.blue_total_points:
                return self.team_red
            elif self.blue_total_points > self.red_total_points:
                return self.team_blue
            else:
                return None

    def save(self, *args, **kwargs):
        if self.is_completed:
            self.winner = self.calculate_winner()
        super().save(*args, **kwargs)


class GameRound(models.Model):
    """Detailed round tracking with player resources"""

    match = models.ForeignKey(
        Match,
        null=True,
        blank=True,
        related_name="game_rounds",
        on_delete=models.CASCADE,
    )
    round_number = models.IntegerField()  # 1 or 2 for matches, 1 for single rounds

    arena_map = models.ForeignKey(
        "core.ArenaMap",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="game_rounds",
    )
    zone_size = models.IntegerField(null=True, blank=True)

    # add a log of events that happened during the round with associated timestamps
    event_log = models.TextField(blank=True, help_text="Log of events during the round")

    # Teams (for single rounds, match can be null)
    team_red = models.ForeignKey(
        Team,
        related_name="red_game_rounds",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    team_blue = models.ForeignKey(
        Team,
        related_name="blue_game_rounds",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    date_played = models.DateTimeField(auto_now_add=True)

    rng_seed = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "63-bit integer seed passed to random.seed() before simulating "
            "this round; null = round predates SIM-07 / not replayable. "
            "Replay is exact only if rosters and map config are unchanged."
        ),
    )

    # RES-04: per-round cell occupancy snapshot. Populated by _flush_to_db
    # when a map is active (movement_ctx is not None); map-less rounds
    # leave this null. JSON shape: {"<player_id>": {"<r>,<c>": tick_count}}.
    cell_occupancy_json = models.JSONField(null=True, blank=True, default=None)

    # RV-02: auto-flagged highlights for this round, built by _flush_to_db at
    # round completion. JSON shape: list of typed records sorted by tick,
    # {kind, tick, team, actor, target, points, label}. Null for rounds that
    # predate RV-02 (no backfill, ADR-0004) — drives the events-page
    # Highlights tab.
    highlights_json = models.JSONField(null=True, blank=True, default=None)

    # Round results
    red_points = models.IntegerField(default=0)
    blue_points = models.IntegerField(default=0)
    red_team_eliminated = models.BooleanField(default=False)
    blue_team_eliminated = models.BooleanField(default=False)
    eliminated_at = models.IntegerField(default=SURVIVED_SENTINEL)

    winner = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        related_name="won_game_rounds",
        on_delete=models.SET_NULL,
    )
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        if self.match:
            return f"{self.team_red.name} vs {self.team_blue.name} - Round {self.round_number}"
        else:
            return f"{self.team_red.name} vs {self.team_blue.name} - Single Round"

    def calculate_winner(self):
        if self.red_points > self.blue_points:
            return self.team_red
        elif self.blue_points > self.red_points:
            return self.team_blue
        else:
            return None

    def save(self, *args, **kwargs):
        if self.is_completed:
            self.winner = self.calculate_winner()
        super().save(*args, **kwargs)

    def get_event_summary(self):
        """Get a summary of events in this round"""
        events = self.events.all()
        return {
            "total_events": events.count(),
            "tags": events.filter(event_type="tag").count(),
            "moves": events.filter(event_type="movement").count(),
            "missiles": events.filter(event_type="missiled").count(),
            "misses": events.filter(event_type="miss").count(),
            "eliminations": events.filter(event_type="elimination").count(),
            "resupplies": events.filter(event_type__startswith="resupply").count(),
            "specials": events.filter(event_type="special").count(),
        }

    def get_player_event_timeline(self, player):
        """Get all events involving a specific player"""
        return self.events.filter(
            models.Q(actor=player) | models.Q(target=player)
        ).order_by("timestamp")

    def get_kill_feed(self):
        """Get chronological list of tags and eliminations for display"""
        return (
            self.events.filter(event_type__in=["tag", "missiled", "elimination"])
            .select_related("actor", "target")
            .order_by("timestamp")
        )


class PlayerRoundState(models.Model):
    """Track individual player resources and performance in a round"""

    class tag_id(models.IntegerChoices):
        none = 0
        red_commander = 1
        red_heavy = 2
        red_scout_1 = 3
        red_scout_2 = 4
        red_ammo = 5
        red_medic = 6
        blue_commander = 7
        blue_heavy = 8
        blue_scout_1 = 9
        blue_scout_2 = 10
        blue_ammo = 11
        blue_medic = 12
        red_base = 13
        blue_base = 14
        neutral_base = 15

    class zones(models.IntegerChoices):
        red_zone = 0
        neutral_zone = 1
        blue_zone = 2

    game_round = models.ForeignKey(
        GameRound, related_name="player_states", on_delete=models.CASCADE
    )
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    team_color = models.CharField(
        max_length=10, choices=[("red", "Red"), ("blue", "Blue")], default="red"
    )
    role = models.CharField(
        max_length=50, default="commander"
    )  # e.g., "Commander", "Ammo"

    # Starting resources
    starting_lives = models.IntegerField(default=15)
    starting_shots = models.IntegerField(default=30)
    starting_special = models.IntegerField(default=3)
    starting_missiles = models.IntegerField(default=0)  # Only for commander/heavy

    # these are for resets, ability to be resupplied, and ability to be tagged
    last_tagged_id = models.IntegerField(choices=tag_id.choices, default=tag_id.none)
    shields = models.IntegerField(
        default=1
    )  # When 0 player loses a life and is down for 8 seconds
    last_downed_time = models.IntegerField(
        null=True, blank=True
    )  # Timestamp of when player was last downed
    neutral_base_destroyed = models.BooleanField(
        default=False
    )  # true if player has destroyed the neutral base
    opposing_base_destroyed = models.BooleanField(
        default=False
    )  # true if player has destroyed the opposing base
    zone_fallback = models.IntegerField(choices=zones.choices, default=zones.red_zone)
    cell_row = models.IntegerField(null=True, blank=True)
    cell_col = models.IntegerField(null=True, blank=True)

    @property
    def current_zone(self) -> int:
        """Zone index (0=red, 1=neutral, 2=blue) derived from zone_fallback.

        In MAP-02+ this will derive from cell_row/cell_col and the map's zone_data.
        For now the simulator keeps zone_fallback in sync so this is a direct read.
        """
        return self.zone_fallback

    special_active_until = models.IntegerField(
        null=True, blank=True, default=0
    )  # Timestamp until which special is active
    is_hiding = models.BooleanField(default=False)

    # Final resources
    final_lives = models.IntegerField(default=0)
    final_shots = models.IntegerField(default=0)
    final_special = models.IntegerField(default=0)
    final_missiles = models.IntegerField(default=0)
    final_medic_hits = models.IntegerField(default=0)

    # Performance stats
    points_scored = models.IntegerField(default=0)
    tags_made = models.IntegerField(default=0)
    shots_missed = models.IntegerField(default=0)
    times_tagged = models.IntegerField(default=0)
    specials_used = models.IntegerField(default=0)
    own_specials_cancelled = models.IntegerField(default=0)
    enemy_nuke_cancels = models.IntegerField(default=0)
    ally_nuke_cancels = models.IntegerField(default=0)
    medic_lives_removed_from_nuke = models.IntegerField(default=0)
    lives_lost_to_nukes = models.IntegerField(default=0)
    missiles_landed = models.IntegerField(default=0)
    times_missiled = models.IntegerField(default=0)
    resupplies_given = models.IntegerField(default=0)
    combo_resupply_count = models.IntegerField(default=0)
    times_tagged_in_reset_window = models.IntegerField(
        default=0
    )  # tagged while taggable but not yet active (4-7s after downed)
    follow_up_shots = models.IntegerField(
        default=0
    )  # shots fired as follow-ups on high-shield targets
    reaction_shots = models.IntegerField(
        default=0
    )  # shots fired as reactions to being tagged/missed
    ticks_active = models.IntegerField(default=0)  # ticks player was fully active
    ticks_not_targetable = models.IntegerField(
        default=0
    )  # ticks player was in the 0-3s post-down untargetable window
    ticks_reset_window = models.IntegerField(
        default=0
    )  # ticks player was in the 4-7s taggable-but-not-active reset window
    missile_points = models.IntegerField(default=0)  # points awarded from missiles

    # detailed performance stats
    specific_tags = models.JSONField(
        default=dict, help_text="Details of tags made: {target_id: count, ...}"
    )
    """
    example:
    {
        "player_id_1": {
            "tags": 3,
            "tagged_by": 1,
            "missiled": 2,
            "missiled_by": 0,
            },
        "player_id_2": {
            "tags": 3,
            "tagged_by": 1,
            "missiled": 2,
            "missiled_by": 0,
            },
    }
    """

    # Status — tick of final elimination; SURVIVED_SENTINEL (1801) = survived
    was_eliminated_at = models.IntegerField(default=SURVIVED_SENTINEL)

    def __str__(self):
        return f"n:{self.player.name} id:{self.player.id} tclr:{self.team_color} rl:{self.role}"

    @property
    def shot_power(self):
        return ROLE_STATS.get(self.role, {}).get("shot_power", 1)

    @property
    def max_shields(self):
        return ROLE_STATS.get(self.role, {}).get("shield", 1)

    @property
    def max_lives(self):
        return MAX_LIVES.get(self.role, 15)

    @property
    def lives_lost(self):
        return max(
            0, self.times_tagged + self.times_missiled * 2 + self.lives_lost_to_nukes
        )

    @property
    def max_shots(self):
        return MAX_SHOTS.get(self.role, 30)

    @property
    def shots_used(self):
        return max(0, self.tags_made + self.shots_missed)

    @property
    def max_special(self):
        return 99  # limit is 99 at a time

    @property
    def special_cost(self):
        special_chart = {
            "commander": 20,
            "heavy": 100,  # Heavy cannot use specials
            "scout": 10,
            "medic": 10,
            "ammo": 15,
        }
        return special_chart.get(
            self.role, 100
        )  # Default to very high cost if role unknown

    @property
    def can_use_special(self):
        return self.final_special >= self.special_cost

    @property
    def can_capture_base_in_current_zone(self):
        if (
            self.current_zone == self.zones.neutral_zone
            and not self.neutral_base_destroyed
        ):
            return True
        elif (
            self.team_color == "red"
            and self.current_zone == self.zones.blue_zone
            and not self.opposing_base_destroyed
        ):
            return True
        elif (
            self.team_color == "blue"
            and self.current_zone == self.zones.red_zone
            and not self.opposing_base_destroyed
        ):
            return True
        return False

    @property
    def missiles_used(self):
        return max(0, self.missiles_landed)

    def is_resupplyable_at(self, seconds_into_round):
        """Return True if the player can be resupplied at the given second into the round."""
        return self.is_active_at(seconds_into_round)

    def is_active_at(self, seconds_into_round):
        """Check if player is active at a given time (not in downed cooldown).

        TIME-01: this method operates in the **seconds** domain. It is only
        reached by ``ResourceBasedSimulator``, which keeps its existing
        second-internal loop (``db_second = int(second)``) for byte-identical
        behaviour. ``BatchSimulator`` is fully tick-native and routes through
        ``PlayerState.is_active_at`` instead (which uses tick thresholds), so
        the 8 s / 4 s respawn literals here must stay in seconds.
        """
        if self.final_lives == 0:
            return False
        if getattr(self, "last_downed_time", None) is not None:
            if seconds_into_round - self.last_downed_time < 8:
                return False
        return True

    def is_taggable_at(self, seconds_into_round):
        """Return True if the player can be tagged at the given second (not in respawn resettime).

        TIME-01: seconds-domain; RBS-only (see ``is_active_at`` note).
        """
        if self.final_lives == 0:
            return False
        if getattr(self, "last_downed_time", None) is not None:
            if seconds_into_round - self.last_downed_time < 4:
                return False
        return True

    def eliminated_timestamp(self):
        """Render the elimination tick as a MM:SS string (DISPLAY boundary).

        TIME-01: ``was_eliminated_at`` is stored in ticks; convert to seconds
        (÷2) before formatting into minutes/seconds.
        """
        total_seconds = int(self.was_eliminated_at * TICK_SECONDS)
        minutes = total_seconds // 60
        secs = total_seconds % 60
        return f"{minutes}:{secs:02d}"

    # ------------------------------------------------------------------ #
    # Forwarding properties matching the PlayerState duck type so shared
    # combat functions in sim_helpers/combat.py work with both state types.
    # These are pure Python (no DB fields) — no migration required.
    # ------------------------------------------------------------------ #

    @property
    def accuracy(self) -> int:
        return self.player.stat_for_simulation("accuracy", self.role)

    @property
    def survival(self) -> int:
        return self.player.stat_for_simulation("survival", self.role)

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def player_awareness(self) -> int:
        return self.player.stat_for_simulation("player_awareness", self.role)

    @property
    def game_awareness(self) -> int:
        return self.player.stat_for_simulation("game_awareness", self.role)

    @property
    def resource_awareness(self) -> int:
        return self.player.stat_for_simulation("resource_awareness", self.role)

    @property
    def decision_making(self) -> int:
        return self.player.stat_for_simulation("decision_making", self.role)

    @property
    def stamina(self) -> int:
        return self.player.stat_for_simulation("stamina", self.role)

    @property
    def special_usage(self) -> int:
        return self.player.stat_for_simulation("special_usage", self.role)

    @property
    def resupply_efficiency(self) -> int:
        return self.player.stat_for_simulation("resupply_efficiency", self.role)

    @property
    def resupply_synergy(self) -> int:
        return self.player.stat_for_simulation("resupply_synergy", self.role)

    @property
    def teamwork(self) -> int:
        return self.player.stat_for_simulation("teamwork", self.role)

    @property
    def communication(self) -> int:
        return self.player.stat_for_simulation("communication", self.role)

    @property
    def speed(self) -> int:
        return self.player.stat_for_simulation("speed", self.role)

    @property
    def stamina_hit_modifier(self) -> float:
        penalty_count = getattr(self, "stamina_penalty_count", 0)
        return max(0.5, 1.0 - 0.05 * penalty_count)

    @property
    def last_shot_time(self) -> float:
        return getattr(self, "_last_shot_time", -99.0)

    @last_shot_time.setter
    def last_shot_time(self, value: float) -> None:
        self._last_shot_time = value

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        saved_shot_time = getattr(self, "_last_shot_time", -99.0)
        saved_stamina_penalty = getattr(self, "stamina_penalty_count", 0)
        saved_stamina_next_check = getattr(self, "stamina_next_check_pct", 10)
        # MECH-06: preserve transient memory fields across DB refresh
        saved_player_memory = getattr(self, "player_memory", {})
        saved_medic_hit_times = getattr(self, "medic_hit_times", [])
        saved_score_broadcast_state = getattr(self, "score_broadcast_state", {})
        saved_score_broadcast_next = getattr(self, "score_broadcast_next", 180)
        saved_scout_index = getattr(self, "_scout_index", 1)
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        self._last_shot_time = saved_shot_time
        self.stamina_penalty_count = saved_stamina_penalty
        self.stamina_next_check_pct = saved_stamina_next_check
        self.player_memory = saved_player_memory
        self.medic_hit_times = saved_medic_hit_times
        self.score_broadcast_state = saved_score_broadcast_state
        self.score_broadcast_next = saved_score_broadcast_next
        self._scout_index = saved_scout_index

    @property
    def tag_id_key(self):
        """Common tag-identity accessor used by choose_tag_target in mechanics.py."""
        return self.get_tag_id

    @property
    def get_tag_id(self):
        # Normalize role comparisons (role strings may be lowercase)
        role = str(self.role).lower() if self.role is not None else ""

        if self.team_color == "red":
            if role == "commander":
                return self.tag_id.red_commander
            elif role == "heavy":
                return self.tag_id.red_heavy
            elif role == "scout":
                try:
                    team = self.player.team
                    if self.player_id == team.slot_scout_1_id:
                        return self.tag_id.red_scout_1
                    return self.tag_id.red_scout_2
                except Exception:
                    return self.tag_id.red_scout_1
            elif role == "ammo":
                return self.tag_id.red_ammo
            elif role == "medic":
                return self.tag_id.red_medic
        elif self.team_color == "blue":
            if role == "commander":
                return self.tag_id.blue_commander
            elif role == "heavy":
                return self.tag_id.blue_heavy
            elif role == "scout":
                try:
                    team = self.player.team
                    if self.player_id == team.slot_scout_1_id:
                        return self.tag_id.blue_scout_1
                    return self.tag_id.blue_scout_2
                except Exception:
                    return self.tag_id.blue_scout_1
            elif role == "ammo":
                return self.tag_id.blue_ammo
            elif role == "medic":
                return self.tag_id.blue_medic
        return self.tag_id.none

    @property
    def string_tag_id(self) -> str:
        """MECH-06: string tag identifier matching PlayerState.tag_id format.

        Returns strings like "red_commander", "blue_scout_1", etc. so that the
        player memory system (which uses string tag IDs) works uniformly across
        both PlayerRoundState and PlayerState objects.
        """
        role = str(self.role).lower() if self.role is not None else ""
        color = self.team_color or ""
        if role == "scout":
            # Use a transient scout index if set (by _initialize_players), else default to 1.
            idx = getattr(self, "_scout_index", 1)
            return f"{color}_scout_{idx}"
        return f"{color}_{role}"

    @property
    def get_accuracy(self):
        """Shot accuracy as an integer percentage (0–100)."""
        total = self.tags_made + self.shots_missed
        if total == 0:
            return 0
        return round(self.tags_made / total * 100)

    @property
    def get_mvp(self) -> float:
        """
        SM5 MVP score following official Laserforce rules.
        The player with the highest score on each team is the round MVP.

        See matches/sim_helpers/score_calculator.py for the full formula.
        """
        from matches.sim_helpers.score_calculator import calculate_mvp

        return calculate_mvp(self)

    @property
    def survival_rate(self):
        """Percentage of lives remaining"""
        if self.starting_lives == 0:
            return 0
        return (self.final_lives / self.starting_lives) * 100


class GameEvent(models.Model):
    """Log of all events that occur during a game round"""

    EVENT_TYPES = [
        ("tag", "Tag"),
        ("locking", "Missile Lock Start"),
        ("missiled", "Missile Resolved"),
        ("special", "Special Activated"),
        ("miss", "Missed Shot"),
        ("resupply_ammo", "Ammo Resupply"),
        ("resupply_lives", "Medic Heal"),
        ("elimination", "Player Eliminated"),
        ("team_elimination", "Team Eliminated"),
        ("nuke_cancelled", "Nuke Cancelled"),
        ("medic_reset", "Medic Reset"),
    ]

    game_round = models.ForeignKey(
        "GameRound", related_name="events", on_delete=models.CASCADE
    )
    timestamp = models.IntegerField(
        help_text="Ticks into the round (0-1800 for a 15 min game; 1 tick = 0.5 s)"
    )
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)

    # Primary actor (the one performing the action)
    actor = models.ForeignKey(
        Player, related_name="events_as_actor", on_delete=models.CASCADE
    )

    # Target (the one receiving the action, if applicable)
    target = models.ForeignKey(
        Player,
        related_name="events_as_target",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    # Event details
    points_awarded = models.IntegerField(
        default=0, help_text="Points awarded for this event"
    )
    description = models.TextField(
        blank=True, help_text="Human-readable description of the event"
    )

    # Additional context (stored as JSON-like text or use JSONField if using PostgreSQL)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional data: lives_remaining, shots_used, special_type, etc.",
    )

    class Meta:
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["game_round", "timestamp"]),
            models.Index(fields=["event_type"]),
            models.Index(fields=["actor"]),
        ]

    def __str__(self):
        if self.target:
            return f"[{self.timestamp}t] {self.actor.name} -> {self.get_event_type_display()} -> {self.target.name}"
        else:
            return f"[{self.timestamp}t] {self.actor.name} -> {self.get_event_type_display()}"

    @property
    def formatted_timestamp(self):
        """Convert the tick timestamp to MM:SS format (DISPLAY boundary).

        TIME-01: ``timestamp`` is stored in ticks; convert to seconds (÷2)
        before formatting into minutes/seconds.
        """
        total_seconds = int(self.timestamp * TICK_SECONDS)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def get_event_icon(self):
        """Return an emoji/icon for display purposes"""
        icons = {
            "tag": "🎯",
            "missiled": "🚀",
            "locking": "🔒",
            "special": "⚡",
            "miss": "❌",
            "movement": "🏃",
            "resupply_ammo": "📦",
            "resupply_lives": "💚",
            "elimination": "💀",
            "team_elimination": "☠️",
            "base_capture": "🚩",
        }
        return icons.get(self.event_type, "•")
