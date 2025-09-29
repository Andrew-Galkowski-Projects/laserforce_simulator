from django.db import models
from django.core.exceptions import ValidationError


class Team(models.Model):
    name = models.CharField(max_length=100)
    created_date = models.DateTimeField(auto_now_add=True)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)

    def __str__(self):
        return self.name

    @property
    def player_count(self):
        return self.players.count()

    @property
    def is_valid_roster(self):
        """
        Check if team has exactly 6 players with correct role distribution
        """
        if self.player_count != 6:
            return False

        roles = self.players.values_list("role", flat=True)
        role_counts = {}
        for role in roles:
            role_counts[role] = role_counts.get(role, 0) + 1

        # Check we have exactly one of each role, except one role should have 2
        unique_roles = set(roles)
        if len(unique_roles) != 5:  # Should have all 5 different roles
            return False

        # One role should have 2 players, others should have 1
        counts = list(role_counts.values())
        counts.sort()
        return counts == [1, 1, 1, 1, 2]


class Player(models.Model):
    ROLES = [
        ("commander", "Commander"),
        ("heavy", "Heavy Weapons"),
        ("scout", "Scout"),
        ("medic", "Medic"),
        ("ammo", "Ammo"),
    ]

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=ROLES)

    # Base stats that affect gameplay
    accuracy = models.IntegerField(default=50)  # 0-100
    survival = models.IntegerField(default=50)  # how well they avoid tags
    special_usage = models.IntegerField(default=50)  # tactical ability

    @property
    def has_missiles(self):
        return self.role in ["commander", "heavy"]

    class Meta:
        unique_together = ["team", "name"]  # No duplicate names within a team

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) - {self.team.name}"

    def clean(self):
        """Validate role constraints"""
        if self.team_id:
            # Check if adding this role would violate constraints
            current_roles = self.team.players.exclude(pk=self.pk).values_list(
                "role", flat=True
            )
            role_counts = {}
            for role in current_roles:
                role_counts[role] = role_counts.get(role, 0) + 1

            # Add the current player's role
            role_counts[self.role] = role_counts.get(self.role, 0) + 1

            # Check constraints: no role can have more than 2 players
            if role_counts[self.role] > 2:
                raise ValidationError(
                    f"Team already has 2 {self.get_role_display()} players"
                )

            # Check total doesn't exceed 6
            if sum(role_counts.values()) > 6:
                raise ValidationError("Team cannot have more than 6 players")
