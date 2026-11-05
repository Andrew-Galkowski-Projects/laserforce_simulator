from django.contrib import admin

from .models import ArenaMap


@admin.register(ArenaMap)
class ArenaMapAdmin(admin.ModelAdmin):
    """MOVE-08: the map's real-world scale (``feet_per_pixel``) is set here;
    a square is ``zone_size x feet_per_pixel`` feet."""

    list_display = ("name", "img_width", "img_height", "feet_per_pixel", "is_default")
    fields = (
        "name",
        "image",
        "is_default",
        "img_width",
        "img_height",
        "feet_per_pixel",
    )
