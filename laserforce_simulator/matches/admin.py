from django.contrib import admin

from .models import League, Season

# Register your models here.


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "mode", "state", "created_at")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "league", "state", "schedule_format", "start_date")
    filter_horizontal = ("teams",)
