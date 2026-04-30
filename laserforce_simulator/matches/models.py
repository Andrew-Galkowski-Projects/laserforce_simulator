from datetime import datetime
from django.db import models
from teams.models import Team, Player


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
    round1_eliminated_at = models.IntegerField(default=901)

    # Round 2 scores (teams switch colors)
    red_round2_points = models.IntegerField(default=0)
    blue_round2_points = models.IntegerField(default=0)
    red_round2_eliminated = models.BooleanField(default=False)
    blue_round2_eliminated = models.BooleanField(default=False)
    round2_eliminated_at = models.IntegerField(default=901)

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

    # Round results
    red_points = models.IntegerField(default=0)
    blue_points = models.IntegerField(default=0)
    red_team_eliminated = models.BooleanField(default=False)
    blue_team_eliminated = models.BooleanField(default=False)
    eliminated_at = models.IntegerField(default=901)

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
            "missiles": events.filter(event_type="missile").count(),
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
            self.events.filter(event_type__in=["tag", "missile", "elimination"])
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
    shot_power = models.IntegerField(default=1)
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
    current_zone = models.IntegerField(
        choices=zones.choices, default=zones.red_zone
    )  # currently a number between 0 and 2
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

    # Status
    was_eliminated_at = models.IntegerField(default=901)  # Ran out of lives

    def __str__(self):
        return f"n:{self.player.name} id:{self.player.id} tclr:{self.team_color} rl:{self.role}"

    @property
    def max_shields(self):
        # Determine max shields based on role
        max_shields = {
            "commander": 3,
            "heavy": 3,
            "scout": 1,
            "medic": 1,
            "ammo": 1,
        }
        return max_shields.get(self.role, 1)  # Default to 1 if role unknown

    @property
    def max_lives(self):
        # Determine max lives based on role
        max_lives = {
            "commander": 30,
            "heavy": 20,
            "scout": 30,
            "medic": 20,
            "ammo": 20,
        }
        return max_lives.get(self.role, 15)  # Default to 15 if role unknown

    @property
    def lives_lost(self):
        return max(0, self.times_tagged + self.times_missiled * 2 + self.lives_lost_to_nukes)

    @property
    def max_shots(self):
        # Determine max shots based on role
        max_shots = {
            "commander": 60,
            "heavy": 40,
            "scout": 60,
            "medic": 30,
            "ammo": 15,
        }
        return max_shots.get(self.role, 30)  # Default to 30 if role unknown

    @property
    def shots_used(self):
        return max(0, self.tags_made + self.shots_missed)

    @property
    def get_accuracy(self):
        return round(
            (
                (self.tags_made + self.resupplies_given)
                / (self.shots_used + self.resupplies_given)
                * 100
            ),
            2,
        )

    @property
    def get_mvp(self):
        total = 0
        accuracy = round(self.get_accuracy / 10, 2)
        medic_hits = self.final_medic_hits
        # 4 + 1/60 per second remaining above 3 min
        three_min_threshold = 780  # in second
        if (self.game_round.blue_team_eliminated and self.team_color == "red") or (
            self.game_round.red_team_eliminated and self.team_color == "blue"
        ):
            elim_bonus = max(
                4,
                (4 + (1 / 60) * (three_min_threshold - self.game_round.eliminated_at)),
            )
        else:
            elim_bonus = 0
        nuke_cancel = self.enemy_nuke_cancels * 3
        own_nuke_cancel = self.ally_nuke_cancels * -3
        missiled = self.times_missiled * -1
        eliminated = -1 if self.role != "medic" and self.was_eliminated_at != 901 else 0
        role_specific = 0
        role_score_bonus = {
            "commander": 10000,
            "heavy": 7000,
            "scout": 6000,
            "ammo": 3000,
            "medic": 2000,
        }
        if self.points_scored > role_score_bonus[self.role]:
            role_specific += (self.points_scored - role_score_bonus[self.role]) / 1000
        if self.role == "commander":
            """
            Missiles: 1 point for missiling an opponent (does not apply to bases).
            Nukes: 1 point for each successful nuke.
            Score bonus: 1 point (applied fractionally) for every 1000 points over 10,000.
            Get nuke canceled: -1 point.
            """
            role_specific += self.missiles_landed
            # add a point per special used, cancelling out any that were cancelled
            role_specific += self.specials_used - self.own_specials_cancelled
            # -1 point per nuke cancelled on top of that
            role_specific -= self.own_specials_cancelled
        elif self.role == "heavy":
            """
            Missiles: 2 points for missiling an opponent (does not apply to bases).
            Score bonus: 1 point (applied fractionally) for every 1000 points over 7000.
            """
            role_specific += self.missiles_landed * 2
        elif self.role == "scout":
            """
            Hits vs. Commander/Heavy: .2 points for every hit on an enemy Heavy or Commander.
            Score bonus: 1 point (applied fractionally) for every 1000 points over 6000.
            """
            three_hit_ids = ["1", "2"] if self.team_color == "blue" else ["7", "8"]
            three_hit_tags = 0
            for id in three_hit_ids:
                three_hit_tags += self.specific_tags[id]["tags"]
            role_specific += three_hit_tags * 0.2
        elif self.role == "ammo":
            """
            Power Boost: 3 points each time you activate power boost.
            Score bonus: 1 point (applied fractionally) for every 1000 points over 3000.
            """
            role_specific += self.specials_used * 3
        elif self.role == "medic":
            """
            Power Boost: 3 points each time you activate power boost.
            Survival Bonus: 2 point if you are still alive when the game clock expires.
            Score bonus: 2 points (applied fractionally) for every 1000 points over 2000.
            """
            role_specific += self.specials_used * 3
            role_specific += 2 if self.was_eliminated_at == 901 else 0
            # NOTE: this bonus is doubled for medics so we have it one time above and a second time here
            if self.points_scored > role_score_bonus[self.role]:
                role_specific += (
                    self.points_scored - role_score_bonus[self.role]
                ) / 1000
        total = (
            accuracy
            + medic_hits
            + elim_bonus
            + nuke_cancel
            + own_nuke_cancel
            + missiled
            + eliminated
            + role_specific
        )
        return round(total, 2)

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
        """Check if player is active at a given time (not in downed cooldown)."""
        if self.final_lives == 0:
            return False
        if getattr(self, "last_downed_time", None) is not None:
            if seconds_into_round - self.last_downed_time < 8:
                return False
        return True

    def is_taggable_at(self, seconds_into_round):
        """Return True if the player can be tagged at the given second (not in respawn resettime)."""
        if self.final_lives == 0:
            return False
        if getattr(self, "last_downed_time", None) is not None:
            if seconds_into_round - self.last_downed_time < 4:
                return False
        return True

    def eliminated_timestamp(self):
        """Take the eliminated at time (0-900) and turn it into a minute/second time and return"""
        minutes = self.was_eliminated_at // 60
        secs = self.was_eliminated_at % 60
        return f"{minutes}:{secs:02d}"

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
                # Determine scout1 vs scout2 by comparing player names on the team
                try:
                    scouts = list(
                        self.player.team.players.filter(role="scout").order_by("name")
                    )
                    if len(scouts) <= 1:
                        return self.tag_id.red_scout_1
                    # If this player's name sorts before the other, they're scout_1
                    if scouts[0].name == self.player.name:
                        return self.tag_id.red_scout_1
                    else:
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
                    scouts = list(
                        self.player.team.players.filter(role="scout").order_by("name")
                    )
                    if len(scouts) <= 1:
                        return self.tag_id.blue_scout_1
                    if scouts[0].name == self.player.name:
                        return self.tag_id.blue_scout_1
                    else:
                        return self.tag_id.blue_scout_2
                except Exception:
                    return self.tag_id.blue_scout_1
            elif role == "ammo":
                return self.tag_id.blue_ammo
            elif role == "medic":
                return self.tag_id.blue_medic
        return self.tag_id.none

    # this one seems kind of useless
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
        ("missile", "Missile Hit"),
        ("special", "Special Activated"),
        ("miss", "Missed Shot"),
        ("resupply_ammo", "Ammo Resupply"),
        ("resupply_lives", "Medic Heal"),
        ("elimination", "Player Eliminated"),
        ("team_elimination", "Team Eliminated"),
    ]

    game_round = models.ForeignKey(
        "GameRound", related_name="events", on_delete=models.CASCADE
    )
    timestamp = models.IntegerField(
        help_text="Seconds into the round (0-900 for 15 min game)"
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
            return f"[{self.timestamp}s] {self.actor.name} -> {self.get_event_type_display()} -> {self.target.name}"
        else:
            return f"[{self.timestamp}s] {self.actor.name} -> {self.get_event_type_display()}"

    @property
    def formatted_timestamp(self):
        """Convert seconds to MM:SS format"""
        minutes = self.timestamp // 60
        seconds = self.timestamp % 60
        return f"{minutes:02d}:{seconds:02d}"

    def get_event_icon(self):
        """Return an emoji/icon for display purposes"""
        icons = {
            "tag": "🎯",
            "missile": "🚀",
            "special": "⚡",
            "miss": "❌",
            "movement": "🏃",
            "resupply_ammo": "📦",
            "resupply_lives": "💚",
            "elimination": "💀",
            "team_elimination": "☠️",
        }
        return icons.get(self.event_type, "•")


# Example usage in your simulation:
"""
# Creating events during simulation:

# Tag event
GameEvent.objects.create(
    game_round=game_round,
    timestamp=45,  # 45 seconds into the round
    event_type='tag',
    actor=attacker_player,
    target=defender_player,
    points_awarded=100,
    description=f"{attacker_player.name} zaps {defender_player.name}",
    metadata={
        'attacker_lives': 12,
        'defender_lives': 14,  # before the tag
        'shots_remaining': 25,
        'distance': 'medium'
    }
)

# Miss event
GameEvent.objects.create(
    game_round=game_round,
    timestamp=47,
    event_type='miss',
    actor=attacker_player,
    target=defender_player,
    points_awarded=0,
    description=f"{attacker_player.name} missed {defender_player.name}",
    metadata={
        'shots_remaining': 24,
        'reason': 'low_accuracy'
    }
)

# Resupply event
GameEvent.objects.create(
    game_round=game_round,
    timestamp=120,
    event_type='resupply_ammo',
    actor=ammo_player,
    target=teammate_player,
    points_awarded=50,
    description=f"{ammo_player.name} resupplied {teammate_player.name}",
    metadata={
        'shots_given': 10,
        'new_shot_count': 35
    }
)

# Elimination event
GameEvent.objects.create(
    game_round=game_round,
    timestamp=345,
    event_type='elimination',
    actor=killer_player,
    target=eliminated_player,
    points_awarded=200,  # Bonus for elimination
    description=f"{eliminated_player.name} was eliminated by {killer_player.name}",
    metadata={
        'final_score': 850,
        'tags_made': 8,
        'time_survived': 345
    }
)

# Special activation (e.g., Scout's rapid fire)
GameEvent.objects.create(
    game_round=game_round,
    timestamp=200,
    event_type='special',
    actor=scout_player,
    points_awarded=0,
    description=f"{scout_player.name} activated Rapid Fire",
    metadata={
        'special_type': 'rapid_fire',
        'duration': 30,
        'specials_remaining': 2
    }
)
"""


class SingleRound(models.Model):
    """Legacy model for simple single rounds - will be replaced by GameRound"""

    team_red = models.ForeignKey(
        Team, related_name="red_rounds", on_delete=models.CASCADE
    )
    team_blue = models.ForeignKey(
        Team, related_name="blue_rounds", on_delete=models.CASCADE
    )
    date_played = models.DateTimeField(auto_now_add=True)

    red_points = models.IntegerField(default=0)
    blue_points = models.IntegerField(default=0)
    red_team_eliminated = models.BooleanField(default=False)
    blue_team_eliminated = models.BooleanField(default=False)

    winner = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        related_name="won_rounds",
        on_delete=models.SET_NULL,
    )
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.team_red.name} vs {self.team_blue.name} (Single Round)"

    def calculate_winner(self):
        """Calculate round winner"""
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
