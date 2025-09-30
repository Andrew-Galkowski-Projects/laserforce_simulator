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

    # do we need role, shot power, and shield on the player class? can we get them from the role
    role = models.CharField(max_length=20, choices=ROLES)
    # shot power and shield are role dependent, we want to get them from the role
    shot_power = models.IntegerField(
        default=1
    )  # 1-3, affects damage dealt when tagging
    shield = models.IntegerField(default=1)  # 1-3, when shield is 0 player loses a life

    # Base stats that affect gameplay
    # stats are broken down into several categories to allow for more nuanced simulation
    player_awareness = models.IntegerField(default=50)  # 0-100
    # how aware are they of players in their zone and adjacent zones
    # we will use this to influence their ability to stay in the same zone as allies as well as hit enemies
    # we can also use this to influence their ability to get resets
    game_awareness = models.IntegerField(default=50)  # 0-100
    # how aware are they of the overall game situation
    # we will use this to give them an opportunity to act when certain events occur
    # examples: nuke cancels, using nukes when hearing enemy at low lives, shot boost, life boost, moving from zone to zone if they hear ememy resupply sounds

    resource_awareness = models.IntegerField(default=50)  # 0-100
    # how aware are they of their own lives and the lives of others
    # we will use this to influence their ability to go back for resources and use specials strategically
    decision_making = models.IntegerField(default=50)  # 0-100
    # how well they make tactical decisions under pressure
    # we will use this to influence how often they act when they have the chance

    positioning = models.IntegerField(default=50)  # 0-100
    # how well they position themselves in the field
    # we will use this to influence their ability to get tags, avoid tags, and stay in the same zone as allies

    stamina = models.IntegerField(default=50)  # 0-100
    # how long they can keep up high activity before needing to rest
    # we will use this to modify their effectiveness over time
    speed = models.IntegerField(default=50)  # 0-100
    # how fast they can move around the field
    # we will use this to influence their ability to act
    flexibility = models.IntegerField(default=50)  # 0-100
    # how well they can use cover and dodge missiles

    adaptability = models.IntegerField(default=50)  # 0-100
    # how well they can adjust to changing situations
    # we will use this to influence if they change their strategy based on how the match is going
    # basically change zones if they have lost gotten tagged a lot recently

    communication = models.IntegerField(default=50)  # 0-100
    # how well they communicate with teammates
    # we will use this to influence how often when they are part of an event that they share that information with teammates in the same and adjacent zones

    teamwork = models.IntegerField(default=50)  # 0-100
    # how well they work with teammates
    # we will use this as a modifier on tags and avoiding tags when teammates are in the same zone

    Offensive_synergy = models.IntegerField(default=50)  # 0-100
    # how well they work with teammates offensively
    # we will use this to influence their ability to tag the same player as teammates
    defensive_synergy = models.IntegerField(default=50)  # 0-100
    # how well they work with teammates defensively
    # we will use this to influence their ability to reset off of allies to tag enemies,
    # and get resupplies after trading tags with enemies
    midfield_synergy = models.IntegerField(default=50)  # 0-100
    # mainly for scouts, heavies, and commanders
    # we will use this to influence player rotations between zones to maintain pressure and map control
    resupply_synergy = models.IntegerField(default=50)  # 0-100
    # how well the player works with their team's other resupply player to "double" allies
    # we will use this to influence how often they can successfully double allies
    resupply_efficiency = models.IntegerField(default=50)  # 0-100
    # how efficiently they aqcuire their resupplies
    # we will use this to influence how long they stay near the resupply players and if they get more resupplies than needed or not

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

            # TODO: change this to validate all roles have 1 except scout which can have 2
            # For now just ensure no more than 2 of any role
            # Check constraints: no role can have more than 2 players
            if role_counts[self.role] > 2:
                raise ValidationError(
                    f"Team already has 2 {self.get_role_display()} players"
                )

            # Check total doesn't exceed 6
            if sum(role_counts.values()) > 6:
                raise ValidationError("Team cannot have more than 6 players")
