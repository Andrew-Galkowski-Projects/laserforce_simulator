import math
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import models, transaction
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
    # LG-01: optional FK to a Season. Sandbox Matches stay season=NULL.
    # SET_NULL — deleting a Season must NOT cascade-delete its Matches.
    season = models.ForeignKey(
        "matches.Season",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="matches",
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

    # RV-03: whether this round was produced by the simulator (vs a future
    # real-game import path). Drives the diagonal "[Simulated]" watermark on
    # the exported PDF report. Existing rows take the default=True (no backfill,
    # ADR-0004 precedent — rng_seed / cell_occupancy_json / highlights_json).
    is_simulated = models.BooleanField(default=True)

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


# ====================================================================
# LG-01 — League / Season foundation
# ====================================================================


class League(models.Model):
    """A League — the container for a sequence of Seasons.

    See ADR-0014 for the model decision. The active-Season invariant
    (≤1 non-completed Season per League) is enforced on the Season
    side via ``Season.clean``.
    """

    MODE_CHOICES = (
        ("sandbox", "Sandbox"),
        ("league", "League"),
        ("multiplayer", "Multiplayer"),
    )
    STATE_CHOICES = (
        ("active", "Active"),
        ("archived", "Archived"),
    )

    name = models.CharField(max_length=100)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="league")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    # LG-01g: the Team this League's user manages (picked by the TEAM >
    # Schedule sidebar entry's default target). Auto-set at League create
    # time to the alphabetically-first generated Team; SET_NULL on Team
    # delete (the LG-01g sidebar / view fallback chain handles None).
    current_team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_in_leagues",
    )
    # Each League owns its own pool of free agents (Players on no
    # competitive roster). The pool is a dedicated Team created at
    # League-create time; ``Team.objects.regular()`` hides any Team
    # referenced here so per-League pools never leak into competitive
    # team lists. SET_NULL on Team delete.
    free_agent_pool = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="free_agent_pool_for",
    )

    def __str__(self) -> str:
        return self.name

    @property
    def active_season(self) -> "Season | None":
        """The single non-completed Season in this League, or None.

        Returns the most-recently-created non-completed Season
        (excludes Seasons in state ``completed``). The active-Season
        invariant (≤1 non-completed Season per League, enforced by
        ``Season.clean``) guarantees this is well-defined.
        """
        return self.seasons.exclude(state="completed").order_by("-id").first()


class Season(models.Model):
    """A Season inside a League — one schedulable round-robin run.

    State machine: ``draft → active → completed``. The active-Season
    invariant (``clean``) ensures at most one non-completed Season
    lives in a given League at any time. See ADR-0014 for the model
    decision and ADR-0015 for the schedule-on-demand algorithm.
    """

    STATE_CHOICES = (
        ("draft", "Draft"),
        ("active", "Active"),
        ("completed", "Completed"),
    )
    SCHEDULE_FORMAT_CHOICES = (("single_round_robin", "Single round-robin"),)
    # LG-01j — per-Season arena map configuration enum.
    MAP_MODE_CHOICES = (
        ("none", "3-zone fallback"),
        ("single", "Single map"),
        ("random_per_round", "Random per Round"),
    )

    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    name = models.CharField(max_length=100)
    start_date = models.DateField()  # required, no default
    teams = models.ManyToManyField(
        "teams.Team",
        related_name="enrolled_seasons",
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="draft")
    schedule_format = models.CharField(
        max_length=32,
        choices=SCHEDULE_FORMAT_CHOICES,
        default="single_round_robin",
    )
    starting_team_ids_json = models.JSONField(null=True, blank=True, default=None)
    champion_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seasons_won",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # LG-01j — per-Season arena map config (picked at create-League time).
    map_mode = models.CharField(
        max_length=32,
        choices=MAP_MODE_CHOICES,
        default="none",
    )
    map_pool = models.ManyToManyField(
        "core.ArenaMap",
        blank=True,
        related_name="seasons_using_pool",
    )
    starting_map_pool_ids_json = models.JSONField(null=True, blank=True, default=None)

    def __str__(self) -> str:
        return f"{self.league.name} — {self.name}"

    def clean(self) -> None:
        """Validate the active-Season invariant.

        Raises ``django.core.exceptions.ValidationError`` if saving
        would yield more than one non-``completed`` Season in this
        League. Excludes ``self`` so re-saving an existing active
        Season does not trip the check against itself.
        """
        conflicting = (
            Season.objects.filter(league=self.league)
            .exclude(state="completed")
            .exclude(pk=self.pk)
        )
        if self.state != "completed" and conflicting.exists():
            raise ValidationError(
                "Only one non-completed Season is allowed per League."
            )
        # LG-01j — defensive enum-value check on map_mode (the field-level
        # ``choices`` already validates this on ``full_clean()``, but a
        # raw admin-side assignment to an unknown literal would otherwise
        # round-trip through ``save()`` unchecked). M2M pool-count rules
        # live form-side (CreateLeagueForm.clean) + admin-side
        # (SeasonAdmin), NOT here — M2M rows aren't visible to
        # ``Model.clean()``.
        valid_map_modes = {value for value, _ in self.MAP_MODE_CHOICES}
        if self.map_mode not in valid_map_modes:
            raise ValidationError({"map_mode": "Unknown map mode."})

    @transaction.atomic
    def start_season(self) -> None:
        """draft -> active transition.

        Validates ``self.teams.count() >= 2``. Snapshots
        ``starting_team_ids_json = sorted([t.id for t in
        self.teams.all()])`` (ascending). Sets ``state="active"``;
        saves.
        """
        if self.teams.count() < 2:
            raise ValidationError(
                "A Season requires at least 2 enrolled teams to start."
            )
        self.starting_team_ids_json = sorted(t.id for t in self.teams.all())
        # LG-01j — snapshot the map pool at activation time, mirroring
        # the ``starting_team_ids_json`` precedent (sorted asc by id for
        # determinism). Empty pool ⇒ ``[]`` (NOT ``None``); ``None``
        # remains the pre-activation sentinel.
        self.starting_map_pool_ids_json = sorted(m.id for m in self.map_pool.all())
        self.state = "active"
        self.save()

    @transaction.atomic
    def complete_if_finished(self) -> None:
        """active -> completed (idempotent).

        No-op if ``self.state != "active"``. Builds the deterministic
        fixture list via ``generate_schedule`` and compares against
        persisted ``GameRound``s (Side-agnostic match on
        ``frozenset({team_red_id, team_blue_id})`` + ``round_number``).
        When every fixture has a matching played Round, flips
        ``state="completed"`` and stamps ``champion_team`` to the
        rank-1 row of ``compute_standings``.
        """
        if self.state != "active":
            return
        if not self._is_finished():
            return
        self._stamp_champion()

    def _is_finished(self) -> bool:
        """True iff every fixture in this Season has a persisted GameRound.

        Side-agnostic match on ``frozenset({team_red_id, team_blue_id})``
        + ``round_number``. Returns False on degenerate inputs
        (snapshot < 2 team ids) so a malformed active Season never
        auto-completes.

        LG-02-Part2a — the fixture list is now sourced through the
        ``scheduled_fixtures()`` chokepoint (which applies the same
        draft-vs-snapshot ``team_ids`` rule and the ``< 2``-team empty
        guard). A phase-less Season produces the identical fixture list
        via the implicit ``round_robin`` fallback, so behaviour is
        unchanged.
        """
        fixtures = self.scheduled_fixtures()
        if not fixtures:
            return False

        rounds_qs = GameRound.objects.filter(match__season=self).select_related("match")
        played_keys: set[tuple[frozenset[int], int]] = set()
        for game_round in rounds_qs:
            match = game_round.match
            if match is None or match.team_red_id is None or match.team_blue_id is None:
                continue
            played_keys.add(
                (
                    frozenset({match.team_red_id, match.team_blue_id}),
                    game_round.round_number,
                )
            )

        for fixture in fixtures:
            key = (
                frozenset({fixture.team_a_id, fixture.team_b_id}),
                fixture.round_number,
            )
            if key not in played_keys:
                return False
        return True

    # ------------------------------------------------------------------
    # LG-02-Part2a — SeasonPhase chokepoint (read-only pure derivations)
    # ------------------------------------------------------------------

    def _implicit_phase(self) -> "SeasonPhase":
        """Build the UNSAVED implicit ``round_robin`` fallback phase.

        Returns a real ``SeasonPhase`` instance constructed with NO
        ``.save()`` (so ``pk is None``). Used by ``ordered_phases()`` for
        a Season with zero persisted phase rows so a phase-less Season is
        indistinguishable downstream from a Season carrying one explicit
        ``round_robin`` phase.
        """
        return SeasonPhase(season=self, ordinal=1, phase_type="round_robin")

    def ordered_phases(self) -> list["SeasonPhase"]:
        """Return this Season's phases in ordinal order.

        When the Season has >= 1 persisted ``SeasonPhase`` row, returns
        ``list(self.phases.all())`` (``Meta.ordering = ["ordinal"]``
        guarantees ordinal order). When the Season has ZERO rows, returns
        a one-element list holding a SINGLE UNSAVED implicit fallback
        phase (``pk is None``) so a phase-less Season is indistinguishable
        downstream from a Season with one explicit ``round_robin`` phase.
        """
        persisted = list(self.phases.all())
        if persisted:
            return persisted
        return [self._implicit_phase()]

    def scheduled_fixtures(self) -> list["ScheduleFixture"]:
        """Return the flat fixture list for this Season's schedule.

        THIS SLICE: returns exactly the ``round_robin`` phase's fixture
        list, sourced via ``generate_schedule(team_ids,
        self.schedule_format)`` where ``team_ids`` is resolved by the
        existing draft-vs-snapshot rule (draft Season ⇒ sorted live M2M;
        active/completed Season ⇒ ``starting_team_ids_json``). Exactly ONE
        ``round_robin`` phase exists this slice (explicit or implicit
        fallback), so the return is the single RR fixture list. NO
        cross-phase composition, NO matchday offsetting.

        Returns ``[]`` when ``team_ids`` has ``< 2`` entries (mirrors the
        guard at every existing call site) — never raises.
        """
        from .schedule_generator import generate_schedule

        if self.state == "draft":
            team_ids = sorted(t.id for t in self.teams.all())
        else:
            team_ids = list(self.starting_team_ids_json or [])

        if len(team_ids) < 2:
            return []

        return generate_schedule(team_ids, self.schedule_format)

    def _stamp_champion(self) -> None:
        """Flip ``state="completed"`` and stamp ``champion_team``.

        Computes Standings via ``compute_standings`` over the Season's
        completed Matches (the 8-key dict shape that mirrors what
        ``season_standings`` view builds) and writes the rank-1 row's
        team as the Season champion. No-op if Standings is empty
        (defensive; the caller — ``complete_if_finished`` — should have
        already verified fixtures are all played).
        """
        from .standings import compute_standings

        team_ids = self.starting_team_ids_json or []
        matches_qs = Match.objects.filter(season=self, is_completed=True)
        completed_matches: list[dict] = []
        for match in matches_qs:
            completed_matches.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    "winner_team_id": match.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                }
            )
        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        rows = compute_standings(completed_matches, enrolled_teams)
        if not rows:
            return
        self.state = "completed"
        self.champion_team = Team.objects.get(pk=rows[0].team_id)
        self.save()


class SeasonPhase(models.Model):
    """LG-02-Part2a — one ordered phase within a Season's schedule.

    A Season's schedule is composed of one or more phases in ``ordinal``
    order. THIS SLICE only ``round_robin`` has behaviour (it resolves
    fixtures via the existing ``Season.schedule_format`` /
    ``generate_schedule``); the ``tournament`` / ``member_night`` values
    are declared in the enum but inert (Part2b/2c). A Season with zero
    persisted ``SeasonPhase`` rows falls back to a single implicit
    ``round_robin`` phase via ``Season.ordered_phases()`` — see that
    method's ``_implicit_phase`` builder (``pk is None``).
    """

    PHASE_TYPE_CHOICES = (
        ("round_robin", "Round-robin"),
        ("tournament", "Tournament"),
        ("member_night", "Member night"),
    )

    season = models.ForeignKey(
        "matches.Season",
        on_delete=models.CASCADE,
        related_name="phases",
    )
    ordinal = models.PositiveSmallIntegerField()  # 1-based; set explicitly
    phase_type = models.CharField(
        max_length=16,
        choices=PHASE_TYPE_CHOICES,
        default="round_robin",
    )
    # LG-02-Part2b — dormant phase columns (nothing reads them this slice).
    # ``schedule_format`` carries the per-phase wire format: a round_robin
    # phase copies ``Season.schedule_format``; a tournament phase is NULL.
    # ``tournament`` is the forward one-directional embed pointer, ALWAYS
    # NULL in Part2b (the build is Part2c).
    schedule_format = models.CharField(max_length=32, null=True, blank=True)
    tournament = models.ForeignKey(
        "matches.Tournament",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="season_phases",
    )

    class Meta:
        ordering = ["ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "ordinal"],
                name="uniq_season_phase_ordinal",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.season} — phase {self.ordinal} ({self.phase_type})"


class Tournament(models.Model):
    """LG-02a — a standalone single-elimination Tournament (Bracket).

    Decoupled from League / Season (sandbox mode). Each Bracket node is one
    existing 2-round ``Match``. State machine ``setup -> active -> completed``;
    Seeding is editable only in ``setup`` (the bracket is built on the
    setup->active transition, mirroring ``Season.start_season``'s draft->active
    M2M lock).
    """

    FORMAT_CHOICES = (
        ("single_elimination", "Single elimination"),
        ("double_elimination", "Double elimination"),
        ("round_robin", "Round robin"),
        ("round_robin_double_elim", "Round robin → Double elimination"),
        ("swiss", "Swiss"),
    )
    STATE_CHOICES = (
        ("setup", "Setup"),  # participants chosen, Seeding editable, bracket NOT built
        ("active", "Active"),  # bracket built + locked, nodes being played
        ("completed", "Completed"),  # champion crowned
    )

    name = models.CharField(max_length=100)
    format = models.CharField(
        max_length=32, choices=FORMAT_CHOICES, default="single_elimination"
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="setup")
    created_at = models.DateTimeField(auto_now_add=True)
    # Stamped by advance logic when the final node resolves. SET_NULL — deleting
    # a Team must NOT cascade-delete the Tournament's history.
    champion = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tournaments_won",
    )
    # LG-02b-2 — per-Bracket-round best-of-N Series escalation. The resolved N
    # for each Bracket node is anchored to its depth from the final (depth 0 =
    # final, 1 = semifinal, 2 = quarterfinal, >= 3 = earlier rounds) and stamped
    # onto every BracketNode at lock time via ``series_length_for_round``. These
    # four slots are set at create-time only and never re-read after lock.
    final_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    semifinal_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    quarterfinal_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    earlier_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    # LG-02c (RR->DE) — the count of top RR-ranked Teams that advance into the
    # Winners bracket (4 / 8 / 16) and the count that advance into the Losers
    # bracket as pre-seeds (0 or wb//2). No choices — the create form's
    # ``rrde_combo`` select is the single source of valid shape combos; the
    # model holds the resolved ints (mirrors ``BracketNode.series_length``).
    # Meaningful only for ``format == "round_robin_double_elim"``; 0 otherwise.
    wb_advancers = models.PositiveSmallIntegerField(default=0)
    lb_advancers = models.PositiveSmallIntegerField(default=0)
    # LG-02c (Swiss) — total number of Swiss rounds. 0 = auto (resolved at lock
    # to ceil(log2(N)), clamped to [1, N-1], then written back here — frozen).
    # No choices. Meaningful only for format == "swiss"; 0 otherwise.
    swiss_rounds = models.PositiveSmallIntegerField(default=0)
    # LG-02x-1 (Random Draw) — an ORTHOGONAL team-assembly axis (NOT a new
    # ``format``). ``preset`` (default) is byte-unchanged: participants are
    # chosen Teams. ``random_draw`` registers individual Players to a pool and
    # a deterministic tier-balanced draw assembles them into marked 6-player
    # Teams; roles re-assign dynamically every Round. Meaningful for any
    # ``format``; the pool/draw/per-Round-roles machinery fires only when
    # ``team_assembly == "random_draw"``. Create-time only.
    TEAM_ASSEMBLY_CHOICES = (
        ("preset", "Preset teams"),
        ("random_draw", "Random draw player pool"),
    )
    team_assembly = models.CharField(
        max_length=16, choices=TEAM_ASSEMBLY_CHOICES, default="preset"
    )
    # LG-02x-1 — how the drawn Teams' role slots are re-assigned each Round.
    # Meaningful only when ``team_assembly == "random_draw"`` (ignored for
    # preset). ``random`` shuffles each team's 6 tier-players into the 6 role
    # slots independently per team per Round; ``per_tier`` draws one
    # tier->slot bijection per Round applied to BOTH teams. Create-time only.
    ROLE_ASSIGNMENT_CHOICES = (
        ("random", "Random per team per Round"),
        ("per_tier", "Per-tier bijection (both teams)"),
    )
    role_assignment_mode = models.CharField(
        max_length=16, choices=ROLE_ASSIGNMENT_CHOICES, default="random"
    )

    def __str__(self) -> str:
        return self.name

    @property
    def is_locked(self) -> bool:
        """True iff state != 'setup' (Seeding can no longer be edited)."""
        return self.state != "setup"

    @transaction.atomic
    def lock_and_build(self) -> None:
        """setup -> active.

        Validates participant count (>= 4), builds the BracketNode tree from
        the current Seeding via ``matches.bracket.build_bracket``, persists
        every node, flips state='active'. Raises
        ``django.core.exceptions.ValidationError`` on count < 4 or
        state != 'setup'.
        """
        from .bracket import (
            build_bracket,
            build_double_elim_bracket,
            build_swiss_round,
            ParticipantSpec,
            series_length_for_depth,
            series_length_for_round,
        )

        if self.state != "setup":
            raise ValidationError("Tournament can only be locked from setup state.")
        participants = list(self.participants.all())
        if len(participants) < 4:
            raise ValidationError("A tournament requires at least 4 participants.")

        # LG-02c — round-robin (incl. the RR seeding stage of RR->DE): a flat set
        # of BracketNode rows, one per fixture from the FULL (double round-robin)
        # output of generate_schedule. No advancement (advances_to /
        # loser_advances_to stay None), no bye chain. For RR->DE the DE finals
        # are NOT built here — they are deferred until the last RR node resolves
        # (see ``build_de_finals_if_rr_finished``).
        if self.format in ("round_robin", "round_robin_double_elim"):
            from .schedule_generator import generate_schedule

            # RR->DE lock-time COUNT validation: the SHAPE (power-of-two wb,
            # lb in {0, wb//2}) is enforced by the create form; the COUNT fit
            # (wb <= n and wb + lb <= n) is validated here.
            if self.format == "round_robin_double_elim":
                n = len(participants)
                if self.wb_advancers > n:
                    raise ValidationError("wb_advancers exceeds participant count.")
                if self.wb_advancers + self.lb_advancers > n:
                    raise ValidationError(
                        "wb_advancers + lb_advancers exceeds participant count."
                    )

            team_ids = [p.team_id for p in participants]
            fixtures = generate_schedule(team_ids)  # full double RR
            seed_by_team = {p.team_id: p.seed for p in participants}
            team_by_id = {p.team_id: p.team for p in participants}
            # position = 0-based index within each matchday, in schedule order.
            pos_by_matchday: dict[int, int] = {}
            for fixture in fixtures:
                pos = pos_by_matchday.get(fixture.matchday, 0)
                BracketNode.objects.create(
                    tournament=self,
                    bracket_round=fixture.matchday,
                    position=pos,
                    bracket_type="round_robin",
                    team_a=team_by_id[fixture.team_a_id],
                    team_b=team_by_id[fixture.team_b_id],
                    seed_a=seed_by_team[fixture.team_a_id],
                    seed_b=seed_by_team[fixture.team_b_id],
                    is_bye=False,
                    advances_to_slot=None,
                    loser_advances_to_slot=None,
                    winner=None,
                    series_length=1,
                )
                pos_by_matchday[fixture.matchday] = pos + 1
            self.state = "active"
            self.save(update_fields=["state"])
            return

        # LG-02c (Swiss) — even-N only, no byes ever; resolve + clamp + freeze
        # the round count; build R1 via the seed "fold"; persist flat,
        # edge-less Swiss nodes (Bo1). Later rounds are DEFERRED to
        # ``advance_swiss_if_round_finished``.
        if self.format == "swiss":
            n = len(participants)
            if n % 2 != 0:
                raise ValidationError("Swiss requires an even number of participants.")

            # Resolve + clamp + freeze the round count.
            total = self.swiss_rounds or math.ceil(math.log2(n))
            total = max(1, min(total, n - 1))
            self.swiss_rounds = total

            # R1 = seed "fold". Sort by Bracket seed asc, split in half,
            # interleave so consecutive pairs are (seed[i], seed[i + n//2]).
            by_seed = sorted(participants, key=lambda p: p.seed)
            half = n // 2
            fold_order: list[int] = []
            for i in range(half):
                fold_order.append(by_seed[i].team_id)
                fold_order.append(by_seed[i + half].team_id)
            seed_by_team = {p.team_id: p.seed for p in participants}
            team_by_id = {p.team_id: p.team for p in participants}

            specs = build_swiss_round(fold_order, seed_by_team, set(), bracket_round=1)
            for spec in specs:
                BracketNode.objects.create(
                    tournament=self,
                    bracket_round=spec.bracket_round,
                    position=spec.position,
                    bracket_type="swiss",
                    team_a=team_by_id[spec.team_a_id],
                    team_b=team_by_id[spec.team_b_id],
                    seed_a=spec.seed_a,
                    seed_b=spec.seed_b,
                    is_bye=False,
                    advances_to_slot=None,
                    loser_advances_to_slot=None,
                    winner=None,
                    series_length=1,
                )
            self.state = "active"
            self.save(update_fields=["state", "swiss_rounds"])
            return

        part_specs = [
            ParticipantSpec(team_id=p.team_id, seed=p.seed) for p in participants
        ]
        is_de = self.format == "double_elimination"
        if is_de:
            specs = build_double_elim_bracket(part_specs)
        else:
            specs = build_bracket(part_specs)

        # LG-02b-2 — depth-from-final escalation. Single-elim resolves N from
        # depth-from-the-final (max bracket_round); DE specs carry an explicit
        # ``depth`` (distance to GF1) instead. The caller resolves each spec's
        # series_length and hands it to the shared persist helper keyed on the
        # full (bracket_type, bracket_round, position) triple.
        total_rounds = max(spec.bracket_round for spec in specs)
        series_length_by_spec: dict[tuple[str, int, int], int] = {}
        for spec in specs:
            if is_de:
                series_length = series_length_for_depth(
                    spec.depth,
                    final=self.final_series_length,
                    semifinal=self.semifinal_series_length,
                    quarterfinal=self.quarterfinal_series_length,
                    earlier=self.earlier_series_length,
                )
            else:
                series_length = series_length_for_round(
                    spec.bracket_round,
                    total_rounds,
                    final=self.final_series_length,
                    semifinal=self.semifinal_series_length,
                    quarterfinal=self.quarterfinal_series_length,
                    earlier=self.earlier_series_length,
                )
            series_length_by_spec[
                (spec.bracket_type, spec.bracket_round, spec.position)
            ] = series_length

        self._persist_elim_specs(specs, series_length_by_spec)

        self.state = "active"
        self.save(update_fields=["state"])

    def _persist_elim_specs(
        self,
        specs: "list[BracketNodeSpec]",
        series_length_by_spec: dict,
    ) -> None:
        """Persist + wire an elimination (single/double-elim or RR->DE finals)
        spec list.

        Runs the elim persist loop, the ``advances_to`` wiring pass, the
        ``loser_advances_to`` wiring pass, and the ``resolve_bye_chain`` cascade
        pass — shared verbatim by ``lock_and_build`` (single/double-elim) and
        ``build_de_finals_if_rr_finished`` (the deferred RR->DE finals build).
        The caller resolves each spec's ``series_length`` (depth-anchored) and
        passes it via ``series_length_by_spec`` keyed on the full
        ``(bracket_type, bracket_round, position)`` triple — so this helper is
        format-agnostic and the single/double-elim persist behaviour stays
        byte-identical.

        ``team_by_id`` is built from ``self.participants`` (every finalist is an
        enrolled participant, for both the lock path and the deferred build).
        """
        from .bracket import resolve_bye_chain

        team_by_id = {p.team_id: p.team for p in self.participants.all()}
        # The node map is keyed by the full (bracket_type, bracket_round,
        # position) triple so a WB and LB node may share (round, position). For
        # single-elim every node is bracket_type="winners", so the triple still
        # resolves uniquely — output byte-unchanged.
        node_by_pos = {}
        for spec in specs:
            node = BracketNode.objects.create(
                tournament=self,
                bracket_round=spec.bracket_round,
                position=spec.position,
                bracket_type=spec.bracket_type,
                team_a=team_by_id.get(spec.team_a_id),
                team_b=team_by_id.get(spec.team_b_id),
                seed_a=spec.seed_a,
                seed_b=spec.seed_b,
                is_bye=spec.is_bye,
                advances_to_slot=spec.advances_to_slot,
                loser_advances_to_slot=spec.loser_advances_to_slot,
                winner=team_by_id.get(spec.winner_id),
                series_length=series_length_by_spec[
                    (spec.bracket_type, spec.bracket_round, spec.position)
                ],
            )
            node_by_pos[(spec.bracket_type, spec.bracket_round, spec.position)] = node

        # Second pass: wire advances_to self-FKs. The advances_to coord is a
        # 2-tuple (bracket_round, position); resolve its destination bracket_type
        # by searching the persisted nodes (a WB/LB final crosses into the GF).
        def _node_at(coord, prefer_bt):
            if (prefer_bt, coord[0], coord[1]) in node_by_pos:
                return node_by_pos[(prefer_bt, coord[0], coord[1])]
            for (bt, br, pos), nd in node_by_pos.items():
                if br == coord[0] and pos == coord[1]:
                    return nd
            return None

        for spec in specs:
            child = node_by_pos[(spec.bracket_type, spec.bracket_round, spec.position)]
            dirty = []
            if spec.advances_to is not None:
                child.advances_to = _node_at(spec.advances_to, spec.bracket_type)
                dirty.append("advances_to")
            # LG-02c — third pass folded in: wire the loser-drop self-FK (the
            # coord is a (bracket_type, round, position) triple).
            if spec.loser_advances_to is not None:
                ld = spec.loser_advances_to
                child.loser_advances_to = node_by_pos.get((ld[0], ld[1], ld[2]))
                dirty.append("loser_advances_to")
            if dirty:
                child.save(update_fields=dirty)

        # Cascade byes so a top seed's bye is reflected in the next round
        # immediately (and, for DE, collapse Drop byes into the LB). A no-op for
        # the RR->DE finals (no byes — wb is a power of two of real teams).
        flat = [_node_to_dict(n) for n in node_by_pos.values()]
        for mut in resolve_bye_chain(flat):
            key = (
                mut.get("bracket_type", "winners"),
                mut["bracket_round"],
                mut["position"],
            )
            parent = node_by_pos[key]
            team = team_by_id.get(mut["team_id"])
            if mut["slot"] == "a":
                parent.team_a = team
                parent.seed_a = mut["seed"]
            else:
                parent.team_b = team
                parent.seed_b = mut["seed"]
            parent.save(update_fields=["team_a", "team_b", "seed_a", "seed_b"])

    def find_next_playable_node(self) -> "BracketNode | None":
        """Delegates to ``matches.bracket.find_next_node`` over this
        Tournament's nodes.

        Returns the lowest (bracket_round, position) node with both team slots
        filled, is_bye=False, and match_id IS NULL. None when nothing is ready
        (or completed).
        """
        from .bracket import find_next_node

        nodes = list(
            self.nodes.select_related(
                "advances_to", "loser_advances_to"
            ).prefetch_related("series_matches")
        )
        flat = [_node_to_dict(n) for n in nodes]
        result = find_next_node(flat)
        if result is None:
            return None
        for node in nodes:
            if (
                node.bracket_type == result["bracket_type"]
                and node.bracket_round == result["bracket_round"]
                and node.position == result["position"]
            ):
                return node
        return None

    def _match_dicts_over_nodes(self, node_qs) -> tuple:
        """Assemble the three ``compute_standings`` seam inputs —
        ``(enrolled_teams, completed_matches, season_rounds)`` — from a queryset
        of resolved Bracket nodes (Bo1).

        Factored out of ``_standings_over_nodes`` so callers that need the raw
        ``completed_matches`` dicts (Swiss's match-score points) can reuse the
        same single multi-join walk rather than re-querying the nodes.
        """
        participants = list(self.participants.select_related("team"))
        enrolled_teams = [(p.team_id, p.team.name) for p in participants]

        nodes = list(
            node_qs.select_related("team_a", "team_b").prefetch_related(
                "series_matches__match__game_rounds"
            )
        )

        completed_matches: list[dict] = []
        season_rounds: list[dict] = []
        for node in nodes:
            if node.winner_id is None:
                continue
            series = list(node.series_matches.all())
            if not series:
                continue
            # Bo1 — exactly one played SeriesMatch once resolved.
            match = series[0].match
            if match is None:
                continue
            completed_matches.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    # node.winner equals match.winner on a clean win and the
                    # break_tie result on a true tie — never None for a
                    # resolved node.
                    "winner_team_id": node.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                    "date_played": match.date_played,
                }
            )
            for gr in match.game_rounds.all():
                season_rounds.append(
                    {
                        "round_id": gr.id,
                        "team_red_id": gr.team_red_id,
                        "team_blue_id": gr.team_blue_id,
                        "red_points": gr.red_points,
                        "blue_points": gr.blue_points,
                        "date_played": gr.date_played,
                    }
                )

        return enrolled_teams, completed_matches, season_rounds

    def _standings_over_nodes(self, node_qs) -> "list[StandingsRow]":
        """Assemble the three ``compute_standings`` seam inputs from a queryset
        of resolved Bracket nodes (Bo1) and return the ranked rows. Shared by
        ``round_robin_standings`` (RR nodes) and ``swiss_standings`` (Swiss
        nodes).
        """
        from .standings import compute_standings

        enrolled_teams, completed_matches, season_rounds = self._match_dicts_over_nodes(
            node_qs
        )
        return compute_standings(completed_matches, enrolled_teams, season_rounds)

    def _match_point_rows(self, node_qs) -> tuple:
        """``compute_standings`` rows with ``league_points`` overridden by the
        per-team 6-point **Match score** sum (``+2`` per Round won, ``+2`` for
        winning the Match), plus the ``completed_matches`` dicts.

        The override is shared by ``round_robin_standings`` (re-ranked by the
        Match-score ladder) and ``swiss_standings`` (re-ranked by the same
        ladder + a Buchholz tiebreak). Returns ``(rows, completed_matches)`` —
        the rows are still in ``compute_standings`` order; the caller re-ranks.
        """
        from dataclasses import replace

        from .standings import compute_standings, match_points_by_team

        enrolled_teams, completed_matches, season_rounds = self._match_dicts_over_nodes(
            node_qs
        )
        rows = compute_standings(completed_matches, enrolled_teams, season_rounds)
        points = match_points_by_team(completed_matches)
        rows = [replace(row, league_points=points.get(row.team_id, 0)) for row in rows]
        return rows, completed_matches

    def round_robin_standings(self) -> "list[StandingsRow]":
        """LG-02c — Standings rows for this round-robin Tournament.

        Assembles the ``compute_standings`` seam inputs from this Tournament's
        resolved round-robin nodes and returns the ranked rows. Used by both the
        engine (champion) and the detail view (standings table). Returns one row
        per enrolled team (zero-filled before any node is played).

        Ranks on **match wins** first, with the accumulated 6-point **Match
        score** (``+2`` per Round won, ``+2`` for winning the Match) as the
        TIEBREAKER between teams level on wins — so a dominant sweep edges out a
        scrappy split win at equal wins, but more wins always ranks higher
        regardless of margin. ``league_points`` carries that per-team Match-score
        sum (the ``"Match Pts"`` column); rows re-rank on the ``(wins desc,
        league_points desc, round_wins desc, total_score desc)`` ladder.
        """
        from .standings import rerank_round_robin

        rows, _ = self._match_point_rows(self.nodes.filter(bracket_type="round_robin"))
        return rerank_round_robin(rows)

    def swiss_standings(self) -> "list[StandingsRow]":
        """LG-02c (Swiss) — Buchholz-re-ranked Standings for this Swiss
        Tournament.

        Swiss ranks on the accumulated 6-point **Match score** (``+2`` per Round
        won, ``+2`` for winning the Match) rather than ``3*wins`` — so a dominant
        sweep (6) outranks a scrappy split win (4) and pairings/Buchholz reflect
        margin, not just the win/loss bit. Each row's ``league_points`` is
        overridden with that per-team Match-score sum; the Buchholz re-rank layer
        (pure) then re-sorts on the locked ladder (``league_points desc →
        Buchholz desc → round_wins desc → total_score desc → team_name asc``) —
        Buchholz sums opponents' ``league_points``, so it inherits the Match-score
        scale automatically.
        """
        from .bracket import swiss_buchholz_rerank

        rows, _ = self._match_point_rows(self.nodes.filter(bracket_type="swiss"))
        opponents_by_team = self._swiss_opponent_graph()
        return swiss_buchholz_rerank(rows, opponents_by_team)

    def _swiss_opponent_graph(self) -> dict[int, list[int]]:
        """LG-02c (Swiss) — the played-pairs opponent graph from the Swiss
        nodes (each Swiss node = one pairing team_a / team_b). Only nodes whose
        both slots are filled count; a rematch contributes to both lists each
        time it was played (Buchholz sums per played pairing).
        """
        graph: dict[int, list[int]] = {}
        for node in self.nodes.filter(bracket_type="swiss"):
            a, b = node.team_a_id, node.team_b_id
            if a is None or b is None:
                continue
            graph.setdefault(a, []).append(b)
            graph.setdefault(b, []).append(a)
        return graph

    def _swiss_played_pairs(self) -> "set[frozenset[int]]":
        """LG-02c (Swiss) — the side-agnostic set of played pairings derived
        from existing Swiss nodes' team_a_id / team_b_id (every node IS a
        pairing).
        """
        pairs: set[frozenset[int]] = set()
        for node in self.nodes.filter(bracket_type="swiss"):
            if node.team_a_id is not None and node.team_b_id is not None:
                pairs.add(frozenset({node.team_a_id, node.team_b_id}))
        return pairs

    @transaction.atomic
    def complete_round_robin_if_finished(self) -> None:
        """LG-02c — crown the Standings leader once every RR node is resolved.

        No-op unless ``format == "round_robin"`` and ``state == "active"``.
        The RR is finished iff every RR node has a winner; then the rank-1
        Standings row becomes the champion and ``state`` flips to
        ``"completed"``. Idempotent (a second call after completion is a no-op
        via the state guard).
        """
        if self.format != "round_robin" or self.state != "active":
            return

        nodes = self.nodes.filter(bracket_type="round_robin")
        if any(node.winner_id is None for node in nodes):
            return

        rows = self.round_robin_standings()
        if not rows:
            return
        self.champion_id = rows[0].team_id
        self.state = "completed"
        self.save(update_fields=["champion", "state"])

    @transaction.atomic
    def build_de_finals_if_rr_finished(self) -> None:
        """LG-02c (RR->DE) — build the deferred Double-elimination finals once
        every RR (seeding-stage) node has resolved.

        Triggered when the last RR node resolves. Guards (in order), no-op
        unless ALL hold:
          1. ``format == "round_robin_double_elim"``.
          2. ``state == "active"``.
          3. every ``bracket_type="round_robin"`` node has a winner.
          4. idempotency: the finals are not already built (no non-RR node
             exists).

        When all guards pass, the top ``wb_advancers`` RR-ranked Teams seed the
        Winners bracket and the next ``lb_advancers`` seed the Losers-bracket
        pre-seeds (the rest are eliminated). The finals are built via
        ``build_rr_de_finals_bracket`` and persisted via the shared
        ``_persist_elim_specs`` helper. The Tournament STAYS ``active`` — the
        champion is crowned later by the DE Grand final.
        """
        from .bracket import (
            ParticipantSpec,
            build_rr_de_finals_bracket,
            series_length_for_depth,
        )

        if self.format != "round_robin_double_elim":
            return
        if self.state != "active":
            return
        # Every RR seeding node must be resolved.
        if self.nodes.filter(bracket_type="round_robin", winner__isnull=True).exists():
            return
        # Idempotency: finals already built ⇒ no-op.
        if self.nodes.exclude(bracket_type="round_robin").exists():
            return

        rows = self.round_robin_standings()
        # Top wb_advancers RR-ranked teams -> WB seeds 1..wb (seed = 1-based RR
        # rank); next lb_advancers -> LB pre-seeds, seeds wb+1..wb+lb; the rest
        # are eliminated (never enter the finals).
        upper = [
            ParticipantSpec(team_id=rows[i].team_id, seed=i + 1)
            for i in range(self.wb_advancers)
        ]
        lower = [
            ParticipantSpec(
                team_id=rows[self.wb_advancers + j].team_id,
                seed=self.wb_advancers + j + 1,
            )
            for j in range(self.lb_advancers)
        ]

        specs = build_rr_de_finals_bracket(upper, lower)
        series_length_by_spec: dict[tuple[str, int, int], int] = {}
        for spec in specs:
            series_length_by_spec[
                (spec.bracket_type, spec.bracket_round, spec.position)
            ] = series_length_for_depth(
                spec.depth,
                final=self.final_series_length,
                semifinal=self.semifinal_series_length,
                quarterfinal=self.quarterfinal_series_length,
                earlier=self.earlier_series_length,
            )

        self._persist_elim_specs(specs, series_length_by_spec)
        # The Tournament STAYS active — the DE Grand final crowns the champion.

    @transaction.atomic
    def advance_swiss_if_round_finished(self) -> None:
        """LG-02c (Swiss) — build the next Swiss round, or crown, when the
        current (highest) Swiss round's last node resolves.

        No-op unless ``format == "swiss"`` and ``state == "active"``. Determine
        the current (highest) Swiss bracket_round; if NOT all its nodes have a
        winner, no-op. If resolved AND ``current_round < swiss_rounds``, build +
        persist the next round's nodes (greedy ranked sweep from
        ``swiss_standings`` + ``_swiss_played_pairs``). If resolved AND
        ``current_round == swiss_rounds``, crown ``swiss_standings()[0]`` and
        complete.
        """
        if self.format != "swiss" or self.state != "active":
            return

        swiss_nodes = list(self.nodes.filter(bracket_type="swiss"))
        current_round = max((n.bracket_round for n in swiss_nodes), default=0)
        if current_round == 0:
            return
        current_nodes = [n for n in swiss_nodes if n.bracket_round == current_round]
        if any(n.winner_id is None for n in current_nodes):
            return  # round not finished

        if current_round < self.swiss_rounds:
            from .bracket import build_swiss_round

            rows = self.swiss_standings()
            ranked_team_ids = [row.team_id for row in rows]
            played_pairs = self._swiss_played_pairs()
            participants = list(self.participants.all())
            seed_by_team = {p.team_id: p.seed for p in participants}
            team_by_id = {p.team_id: p.team for p in participants}
            specs = build_swiss_round(
                ranked_team_ids,
                seed_by_team,
                played_pairs,
                bracket_round=current_round + 1,
            )
            for spec in specs:
                BracketNode.objects.create(
                    tournament=self,
                    bracket_round=spec.bracket_round,
                    position=spec.position,
                    bracket_type="swiss",
                    team_a=team_by_id[spec.team_a_id],
                    team_b=team_by_id[spec.team_b_id],
                    seed_a=spec.seed_a,
                    seed_b=spec.seed_b,
                    is_bye=False,
                    advances_to_slot=None,
                    loser_advances_to_slot=None,
                    winner=None,
                    series_length=1,
                )
            # Tournament STAYS active.
        else:
            rows = self.swiss_standings()
            if rows:
                self.champion_id = rows[0].team_id
                self.state = "completed"
                self.save(update_fields=["champion", "state"])


class TournamentParticipant(models.Model):
    """LG-02a — one Team's enrolment + Bracket seed in a Tournament."""

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="participants"
    )
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="+")
    # 1-based Bracket seed. Lower int = stronger seed. Unique per Tournament.
    seed = models.PositiveIntegerField()

    class Meta:
        ordering = ["seed"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "seed"], name="uniq_tournament_seed"
            ),
            models.UniqueConstraint(
                fields=["tournament", "team"], name="uniq_tournament_team"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tournament.name} #{self.seed} {self.team.name}"


class BracketNode(models.Model):
    """LG-02a — one node = one slot for a single ``Match`` in a Bracket."""

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="nodes"
    )
    # 1-based Bracket round (1 = first round played; max = final).
    bracket_round = models.PositiveIntegerField()
    # 0-based position within the Bracket round, top-to-bottom in the tree.
    position = models.PositiveIntegerField()

    # The two team slots. Either may be NULL pre-Advancement (a later-round node
    # whose feeder nodes have not resolved yet). SET_NULL on Team delete.
    team_a = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    team_b = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The Bracket seed integers parked alongside each slot, so the tie-break can
    # break on "higher Bracket seed" without re-querying participants and so a
    # bye node can carry its single team's seed forward. NULL when slot empty.
    seed_a = models.PositiveIntegerField(null=True, blank=True)
    seed_b = models.PositiveIntegerField(null=True, blank=True)

    # Advancement pointer: the parent node this node's winner feeds into (NULL
    # for the final node). slot tells the parent which side to fill.
    advances_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feeders",
    )
    advances_to_slot = models.CharField(
        max_length=1,
        null=True,
        blank=True,
        choices=(("a", "team_a"), ("b", "team_b")),
    )
    # A round-1 node a top Bracket seed skips (auto-advanced; never played).
    is_bye = models.BooleanField(default=False)
    # The Team that won (or auto-advanced through) this node. NULL until resolved.
    winner = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # LG-02b-2 — the resolved best-of-N Series length for this node, stamped at
    # lock time by ``Tournament.lock_and_build`` via ``series_length_for_round``
    # (depth-from-final escalation). default=1 so a pre-stamp / bye node reads
    # Bo1. No choices — the four Tournament fields own validation; the node
    # carries the already-resolved int (mirrors how seed_a/seed_b carry ints).
    series_length = models.PositiveSmallIntegerField(default=1)

    # LG-02c — sub-bracket discriminator. Single-elim rows default "winners".
    bracket_type = models.CharField(
        max_length=12,
        choices=(
            ("winners", "Winners bracket"),
            ("losers", "Losers bracket"),
            ("grand_final", "Grand final"),
            ("round_robin", "Round robin"),
            ("swiss", "Swiss"),
        ),
        default="winners",
    )
    # LG-02c — Drop pointer: where THIS node's LOSER goes (parallels advances_to
    # / advances_to_slot which carry the WINNER). NULL for LB nodes (their loser
    # is eliminated) and for GF2. SET_NULL — deleting a node must not cascade.
    loser_advances_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="loser_feeders",
    )
    loser_advances_to_slot = models.CharField(
        max_length=1,
        null=True,
        blank=True,
        choices=(("a", "team_a"), ("b", "team_b")),
    )

    class Meta:
        ordering = ["bracket_round", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "bracket_type", "bracket_round", "position"],
                name="uniq_tournament_bracket_round_position",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tournament.name} R{self.bracket_round}/{self.position}"


class SeriesMatch(models.Model):
    """LG-02b — one Match within a Bracket node's best-of-N Series."""

    node = models.ForeignKey(
        "matches.BracketNode",
        on_delete=models.CASCADE,
        related_name="series_matches",
    )
    match = models.ForeignKey(
        "matches.Match",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="series_match",
    )
    game_number = models.PositiveIntegerField()
    winner = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["game_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "game_number"], name="uniq_seriesmatch_node_game"
            )
        ]

    def __str__(self) -> str:
        return f"{self.node} game {self.game_number}"


class TournamentPlayerEntry(models.Model):
    """LG-02x-1 — a Player's registration in a Random-Draw Tournament's pool,
    AND the durable record of the draw result.

    The source of truth for ``(player, tier, drawn_team)``. A drawn Team's
    ``slot_*`` FKs hold only the transient per-Round role assignment; the
    (player, tier, team) truth lives here. ``tier`` is ``null`` after pool
    intake / before the draw, ``1..6`` after (tier 1 = strongest band).
    """

    tournament = models.ForeignKey(
        "matches.Tournament",
        on_delete=models.CASCADE,
        related_name="player_entries",
    )
    player = models.ForeignKey(
        "teams.Player",
        on_delete=models.CASCADE,
        related_name="tournament_entries",
    )
    tier = models.PositiveSmallIntegerField(
        null=True, blank=True
    )  # 1..6, null pre-draw
    drawn_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drawn_player_entries",
    )

    class Meta:
        ordering = ["tournament_id", "tier", "player_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "player"],
                name="uniq_tournament_player_entry",
            )
        ]

    def __str__(self) -> str:
        return f"{self.tournament.name} :: {self.player.name} (tier {self.tier})"


def count_series_wins(series_matches, team_a_id, team_b_id) -> tuple[int, int]:
    """LG-02b — tally a Bracket node's Series wins per slot from an iterable of
    ``SeriesMatch`` rows. Single source for the ``(wins_a, wins_b)`` derivation
    shared by ``_node_to_dict``, the detail view, and the play engine.
    """
    wins_a = 0
    wins_b = 0
    for sm in series_matches:
        if sm.winner_id is None:
            continue
        if sm.winner_id == team_a_id:
            wins_a += 1
        elif sm.winner_id == team_b_id:
            wins_b += 1
    return wins_a, wins_b


def _node_to_dict(node: "BracketNode") -> dict:
    """Flatten a BracketNode ORM row to the plain dict shape the pure
    ``matches.bracket`` functions consume (LG-02a seam helper).
    """
    advances_to = None
    if node.advances_to_id is not None:
        adv = node.advances_to
        advances_to = (adv.bracket_round, adv.position)
    # LG-02c — loser-drop coord is a 3-tuple (bracket_type, round, position) —
    # the WB->LB Drop crosses brackets, so the coord must carry the destination
    # bracket. (advances_to stays a 2-tuple — deliberate asymmetry.)
    loser_advances_to = None
    if node.loser_advances_to_id is not None:
        ldest = node.loser_advances_to
        loser_advances_to = (ldest.bracket_type, ldest.bracket_round, ldest.position)
    # LG-02b — Series wins per slot (the caller prefetches ``series_matches``).
    wins_a, wins_b = count_series_wins(
        node.series_matches.all(), node.team_a_id, node.team_b_id
    )
    return {
        "bracket_round": node.bracket_round,
        "position": node.position,
        "team_a_id": node.team_a_id,
        "team_b_id": node.team_b_id,
        "seed_a": node.seed_a,
        "seed_b": node.seed_b,
        "is_bye": node.is_bye,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "series_length": node.series_length,
        "winner_id": node.winner_id,
        "advances_to": advances_to,
        "advances_to_slot": node.advances_to_slot,
        # LG-02c — single-elim rows yield ("winners", None, None).
        "bracket_type": node.bracket_type,
        "loser_advances_to": loser_advances_to,
        "loser_advances_to_slot": node.loser_advances_to_slot,
    }
