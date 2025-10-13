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

    # Round 2 scores (teams switch colors)
    red_round2_points = models.IntegerField(default=0)
    blue_round2_points = models.IntegerField(default=0)
    red_round2_eliminated = models.BooleanField(default=False)
    blue_round2_eliminated = models.BooleanField(default=False)

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
    team_color = models.CharField(max_length=10, choices=[("red", "Red"), ("blue", "Blue")], default="red")
    role = models.CharField(max_length=50, default="commander")  # e.g., "Commander", "Ammo"

    # Starting resources
    starting_lives = models.IntegerField(default=15)
    starting_shots = models.IntegerField(default=30)
    starting_special = models.IntegerField(default=3)
    starting_missiles = models.IntegerField(default=0)  # Only for commander/heavy

    # these are for resets, ability to be resupplied, and ability to be tagged
    # this will need to be tweaked later when bases are implemented
    last_tagged_id = models.IntegerField(choices=tag_id.choices, default=tag_id.none)
    shot_power = models.IntegerField(default=1)
    shields = models.IntegerField(default=1)  # When 0 player loses a life and is down for 8 seconds
    is_active = models.BooleanField(default=True)  # false if player is deactivated
    is_taggable = models.BooleanField(default=True)  # false if player is in respawn downtime (4 seconds)
    neutral_base_destroyed = models.BooleanField(default=False)  # true if player has destroyed the neutral base
    opposing_base_destroyed = models.BooleanField(default=False)  # true if player has destroyed the opposing base
    current_zone = models.IntegerField(choices=zones.choices, default=zones.red_zone) # currently a number between 0 and 2


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
    missiles_fired = models.IntegerField(default=0)
    times_missiled = models.IntegerField(default=0)

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
    was_eliminated = models.BooleanField(default=False)  # Ran out of lives

    def __str__(self):
        return f"{self.player.name} - {self.game_round}"

    @property
    def lives_lost(self):
        # TODO: add lives lost due to nukes
        return max(0, self.times_tagged + self.times_missiled * 2)

    @property
    def shots_used(self):
        return max(0, self.tags_made + self.shots_missed)

    @property
    def missiles_used(self):
        return max(0, self.missiles_fired)

    @property
    def is_resupplyable(self):
        return self.is_active
    
    @property
    def get_tag_id(self):
        if self.team_color == "red":
            if self.role == "Commander":
                return self.tag_id.red_commander
            elif self.role == "Heavy":
                return self.tag_id.red_heavy
            elif self.role == "Scout 1":
                return self.tag_id.red_scout_1
            elif self.role == "Scout 2":
                return self.tag_id.red_scout_2
            elif self.role == "Ammo":
                return self.tag_id.red_ammo
            elif self.role == "Medic":
                return self.tag_id.red_medic
        elif self.team_color == "blue":
            if self.role == "Commander":
                return self.tag_id.blue_commander
            elif self.role == "Heavy":
                return self.tag_id.blue_heavy
            elif self.role == "Scout 1":
                return self.tag_id.blue_scout_1
            elif self.role == "Scout 2":
                return self.tag_id.blue_scout_2
            elif self.role == "Ammo":
                return self.tag_id.blue_ammo
            elif self.role == "Medic":
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
