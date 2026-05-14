from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from matches.sim_helpers.role_constants import ROLE_STATS

ROLE_CHOICES = [
    ("commander", "Commander"),
    ("heavy", "Heavy Weapons"),
    ("scout", "Scout"),
    ("medic", "Medic"),
    ("ammo", "Ammo"),
]


class Team(models.Model):
    name = models.CharField(max_length=100)
    created_date = models.DateTimeField(auto_now_add=True)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)

    slot_commander = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    slot_heavy = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    slot_scout_1 = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    slot_scout_2 = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    slot_medic = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    slot_ammo = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return self.name

    @property
    def player_count(self):
        return self.players.count()

    @property
    def players_qs(self):
        return self.players.all()

    @property
    def players_list(self):
        return list(self.players.all())

    @property
    def active_roster(self):
        """Ordered list of (role, player) tuples for filled slots."""
        slots = [
            ("commander", self.slot_commander),
            ("heavy", self.slot_heavy),
            ("scout", self.slot_scout_1),
            ("scout", self.slot_scout_2),
            ("medic", self.slot_medic),
            ("ammo", self.slot_ammo),
        ]
        return [(role, player) for role, player in slots if player is not None]

    @property
    def active_players(self):
        """Players assigned to active slots."""
        return [player for _, player in self.active_roster]

    @property
    def bench_players(self):
        """Players on the team not assigned to any slot."""
        active_ids = {p.pk for p in self.active_players}
        return list(self.players.exclude(pk__in=active_ids))

    @property
    def is_valid_roster(self):
        return not self.roster_errors

    @property
    def roster_errors(self):
        """Return a list of human-readable problems, or [] if valid.

        SM5 roster rules:
        - All 6 slots must be filled
        - Each player appears exactly once (no player can fill multiple slots)
        - Only the Scout ROLE can appear twice (two different Scout players)
        - Players must belong to the team
        """
        errors = []
        all_slots = [
            ("Commander", self.slot_commander, "commander"),
            ("Heavy", self.slot_heavy, "heavy"),
            ("Scout 1", self.slot_scout_1, "scout"),
            ("Scout 2", self.slot_scout_2, "scout"),
            ("Medic", self.slot_medic, "medic"),
            ("Ammo", self.slot_ammo, "ammo"),
        ]
        filled = []
        for slot_name, player, role in all_slots:
            if player is None:
                errors.append(f"missing {slot_name}")
            else:
                filled.append((player, role, slot_name))

        # Check for duplicate players (each player can only appear once)
        player_slots = {}
        for player, role, slot_name in filled:
            if player.pk not in player_slots:
                player_slots[player.pk] = []
            player_slots[player.pk].append((role, slot_name))

        has_duplicate_players = False
        for player_id, slots in player_slots.items():
            if len(slots) > 1:
                # Player appears multiple times — this is always invalid
                has_duplicate_players = True
                slot_names = ", ".join([sname for _, sname in slots])
                # Get player name from filled list
                for p, _, sname in filled:
                    if p.pk == player_id:
                        errors.append(
                            f"{p.name} cannot fill multiple slots: {slot_names}"
                        )
                        break

        # Only check role distribution if there are no duplicate players
        if not has_duplicate_players:
            role_counts = {}
            for player, role, slot_name in filled:
                role_counts[role] = role_counts.get(role, 0) + 1

            for role, count in role_counts.items():
                if count > 2:
                    role_display = dict(ROLE_CHOICES)[role]
                    errors.append(f"{role_display} appears {count} times (max is 2)")
                elif count == 2 and role != "scout":
                    role_display = dict(ROLE_CHOICES)[role]
                    errors.append(
                        f"{role_display} cannot appear twice (Scout-only rule)"
                    )

        # Check that all players belong to team
        for player, role, slot_name in filled:
            if player.team_id != self.pk:
                errors.append(f"{player.name} does not belong to this team")

        return errors


class Player(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    name = models.CharField(max_length=100)
    # TODO: Apply stat boost when player is assigned to a preferred role
    preferred_roles = models.JSONField(default=list, blank=True)

    _STAT_VALIDATORS = [MinValueValidator(0), MaxValueValidator(100)]

    # Base stats that affect gameplay (0–100)
    player_awareness = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    game_awareness = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    resource_awareness = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    decision_making = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    positioning = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    stamina = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    speed = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    flexibility = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    adaptability = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    communication = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    teamwork = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    Offensive_synergy = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    defensive_synergy = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    midfield_synergy = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    resupply_synergy = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    resupply_efficiency = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    accuracy = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    survival = models.IntegerField(default=50, validators=_STAT_VALIDATORS)
    special_usage = models.IntegerField(default=50, validators=_STAT_VALIDATORS)

    @property
    def overall_rating(self):
        stats = [
            self.player_awareness,
            self.game_awareness,
            self.resource_awareness,
            self.decision_making,
            self.positioning,
            self.stamina,
            self.speed,
            self.flexibility,
            self.adaptability,
            self.communication,
            self.teamwork,
            self.Offensive_synergy,
            self.defensive_synergy,
            self.midfield_synergy,
            self.resupply_synergy,
            self.resupply_efficiency,
            self.accuracy,
            self.survival,
            self.special_usage,
        ]
        return sum(stats) / len(stats)

    class Meta:
        unique_together = ["team", "name"]

    def __str__(self):
        prefs = ", ".join(self.preferred_roles) if self.preferred_roles else "none"
        return f"{self.name} (prefers: {prefs}) - {self.team.name}"

    def clean(self):
        valid_roles = {r for r, _ in ROLE_CHOICES}
        invalid = [r for r in (self.preferred_roles or []) if r not in valid_roles]
        if invalid:
            raise ValidationError(
                f"Invalid role(s) in preferred_roles: {', '.join(invalid)}"
            )
