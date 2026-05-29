from django.contrib import admin

from .models import League, Season

# Register your models here.


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "mode", "state", "created_at")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "league", "state", "schedule_format", "start_date")
    # LG-01j — extend the M2M dual-select widget to cover the new
    # ``map_pool`` field alongside the existing ``teams``.
    filter_horizontal = ("teams", "map_pool")
