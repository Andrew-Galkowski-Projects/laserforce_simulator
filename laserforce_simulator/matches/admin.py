from django.contrib import admin

from .models import (
    BracketNode,
    League,
    Season,
    Tournament,
    TournamentParticipant,
    TournamentPlayerEntry,
)

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


# LG-02a — Tournament admin.


class TournamentParticipantInline(admin.TabularInline):
    model = TournamentParticipant
    extra = 0


class BracketNodeInline(admin.TabularInline):
    model = BracketNode
    extra = 0
    fk_name = "tournament"


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "state", "champion", "created_at")
    inlines = (TournamentParticipantInline, BracketNodeInline)


@admin.register(TournamentParticipant)
class TournamentParticipantAdmin(admin.ModelAdmin):
    list_display = ("tournament", "seed", "team")


@admin.register(BracketNode)
class BracketNodeAdmin(admin.ModelAdmin):
    list_display = (
        "tournament",
        "bracket_round",
        "position",
        "team_a",
        "team_b",
        "is_bye",
        "winner",
    )


# LG-02x-1 — Random-Draw player-pool entries.


@admin.register(TournamentPlayerEntry)
class TournamentPlayerEntryAdmin(admin.ModelAdmin):
    list_display = ("tournament", "player", "tier", "drawn_team")
