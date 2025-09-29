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
                return None  # True tie

    def save(self, *args, **kwargs):
        if self.is_completed:
            self.winner = self.calculate_winner()
        super().save(*args, **kwargs)


class SingleRound(models.Model):
    """For casual single-round games"""

    team_red = models.ForeignKey(
        Team, related_name="red_rounds", on_delete=models.CASCADE
    )
    team_blue = models.ForeignKey(
        Team, related_name="blue_rounds", on_delete=models.CASCADE
    )
    date_played = models.DateTimeField(auto_now_add=True)

    red_points = models.IntegerField(default=0)
    blue_points = models.IntegerField(default=0)
    red_eliminated = models.BooleanField(default=False)
    blue_eliminated = models.BooleanField(default=False)

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
            return None  # Tie

    def save(self, *args, **kwargs):
        if self.is_completed:
            self.winner = self.calculate_winner()
        super().save(*args, **kwargs)


# this might be deprecated
class GameRound(models.Model):
    match = models.ForeignKey(Match, null=True, blank=True, on_delete=models.CASCADE)
    round_number = models.IntegerField()  # 1 or 2
    red_team_eliminated = models.BooleanField(default=False)
    blue_team_eliminated = models.BooleanField(default=False)


class PlayerRoundState(models.Model):
    game_round = models.ForeignKey(GameRound, on_delete=models.CASCADE)
    player = models.ForeignKey(Player, on_delete=models.CASCADE)

    # Starting resources
    lives = models.IntegerField(default=15)  # typical starting lives
    shots = models.IntegerField(default=30)  # varies by role
    special_points = models.IntegerField(default=3)
    missiles = models.IntegerField(default=0)  # only for commander/heavy

    # End state
    final_lives = models.IntegerField(default=0)
    final_shots = models.IntegerField(default=0)
    points_scored = models.IntegerField(default=0)


class TagEvent(models.Model):
    game_round = models.ForeignKey(GameRound, on_delete=models.CASCADE)
    tagger = models.ForeignKey(
        Player, related_name="tags_made", on_delete=models.CASCADE
    )
    tagged = models.ForeignKey(
        Player, related_name="tags_received", on_delete=models.CASCADE
    )
    timestamp = models.IntegerField()  # seconds into round
    tag_type = models.CharField(
        max_length=20, default="normal"
    )  # normal, missile, special
    points_awarded = models.IntegerField(default=100)  # base tag value
