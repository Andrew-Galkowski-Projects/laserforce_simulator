from django.db import models


class ArenaMap(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to="maps/")
    is_default = models.BooleanField(default=False)
    img_width = models.IntegerField(null=True)
    img_height = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def latest_confirmed_config(self):
        return self.zone_configs.filter(confirmed=True).order_by("-created_at").first()


class MapZoneConfig(models.Model):
    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="zone_configs"
    )
    zone_size = models.IntegerField()
    zone_data = models.JSONField()
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.arena_map.name} — {self.zone_size}px zones"


VALID_BASE_TYPES = {"red", "blue", "neutral_1", "neutral_2", "neutral_3", "neutral_4"}


class MapBaseConfig(models.Model):
    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="base_configs"
    )
    base_type = models.CharField(max_length=20)
    x_px = models.IntegerField()
    y_px = models.IntegerField()

    class Meta:
        unique_together = [("arena_map", "base_type")]

    def __str__(self):
        return f"{self.arena_map.name} — {self.base_type}"


class SightLineConfig(models.Model):
    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="sight_configs"
    )
    zone_size = models.IntegerField()
    sight_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("arena_map", "zone_size")]

    def __str__(self):
        return f"{self.arena_map.name} — sight lines {self.zone_size}px"


class BaseSightLineConfig(models.Model):
    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="base_sight_configs"
    )
    base_type = models.CharField(max_length=20)
    zone_size = models.IntegerField()
    visible_cells = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("arena_map", "base_type", "zone_size")]

    def __str__(self):
        return f"{self.arena_map.name} — {self.base_type} sight {self.zone_size}px"


class MapCellRankingConfig(models.Model):
    """Cells sorted by LOS count descending, precomputed when sight lines are saved.

    Used by scouts to navigate toward high-visibility positions and by
    medics/ammos to pick sheltered vs exposed positions near the allied heavy.
    ranked_cells is [[row, col], ...] ordered highest-LOS first.
    """

    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="cell_rankings"
    )
    zone_size = models.IntegerField()
    ranked_cells = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("arena_map", "zone_size")]

    def __str__(self):
        return f"{self.arena_map.name} — cell LOS ranking {self.zone_size}px"


class HeavyStrongSpotsConfig(models.Model):
    """Strategically valuable cells for Heavy defensive positioning.

    Shared by both team colours (each Heavy picks the nearest spot to their
    current cell). Auto-seeded when sight lines are computed; user-overridable
    via the map editor Strong Spots mode.
    cells is [[row, col], ...].
    """

    arena_map = models.ForeignKey(
        ArenaMap, on_delete=models.CASCADE, related_name="strong_spots_configs"
    )
    zone_size = models.IntegerField()
    cells = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("arena_map", "zone_size")]

    def __str__(self):
        return f"{self.arena_map.name} — heavy strong spots {self.zone_size}px"
