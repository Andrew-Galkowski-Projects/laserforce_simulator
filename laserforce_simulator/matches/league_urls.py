"""LG-01a — League URL patterns mounted at ``/leagues/`` by the project
URLconf. No ``app_name`` so reverse uses the bare name ``league_list``.

LG-01h extends with 15 placeholder routes (3 League-scoped, 3
Team-scoped, 6 Players-scoped, 6 Stats-scoped) routed through the
shared :func:`matches.views.coming_soon` view.
"""

from django.urls import path

from . import league_views, views

urlpatterns = [
    path("create/", league_views.league_create, name="league_create"),
    path("<int:league_id>/", league_views.league_dashboard, name="league_dashboard"),
    path(
        "<int:league_id>/next-season/",
        league_views.next_season,
        name="next_season",
    ),
    path(
        "<int:league_id>/history/",
        league_views.league_history,
        name="league_history",
    ),
    path(
        "<int:league_id>/team_schedule/<int:team_id>/",
        league_views.team_schedule,
        name="team_schedule",
    ),
    # ---- LG-01h placeholder routes ----
    # League-scoped (3)
    path(
        "<int:league_id>/playoffs/",
        views.coming_soon,
        {"feature_key": "league_playoffs"},
        name="coming_soon_playoffs",
    ),
    path(
        "<int:league_id>/finances/",
        views.coming_soon,
        {"feature_key": "league_finances"},
        name="coming_soon_finances",
    ),
    path(
        "<int:league_id>/power-rankings/",
        views.coming_soon,
        {"feature_key": "league_power_rankings"},
        name="coming_soon_power_rankings",
    ),
    # Team-scoped (3) — keyed by <int:league_id>/team/<slug>/ per LG-01h
    # contract; the Team is resolved internally via league.current_team.
    path(
        "<int:league_id>/team/roster/",
        views.coming_soon,
        {"feature_key": "team_roster"},
        name="coming_soon_team_roster",
    ),
    path(
        "<int:league_id>/team/finances/",
        views.coming_soon,
        {"feature_key": "team_finances"},
        name="coming_soon_team_finances",
    ),
    path(
        "<int:league_id>/team/history/",
        views.coming_soon,
        {"feature_key": "team_history"},
        name="coming_soon_team_history",
    ),
    # Players-scoped (6)
    path(
        "<int:league_id>/players/free-agents/",
        views.coming_soon,
        {"feature_key": "players_free_agents"},
        name="coming_soon_free_agents",
    ),
    path(
        "<int:league_id>/players/trade/",
        views.coming_soon,
        {"feature_key": "players_trade"},
        name="coming_soon_trade",
    ),
    path(
        "<int:league_id>/players/trading-block/",
        views.coming_soon,
        {"feature_key": "players_trading_block"},
        name="coming_soon_trading_block",
    ),
    path(
        "<int:league_id>/players/prospects/",
        views.coming_soon,
        {"feature_key": "players_prospects"},
        name="coming_soon_prospects",
    ),
    path(
        "<int:league_id>/players/watch-list/",
        views.coming_soon,
        {"feature_key": "players_watch_list"},
        name="coming_soon_watch_list",
    ),
    path(
        "<int:league_id>/players/hall-of-fame/",
        views.coming_soon,
        {"feature_key": "players_hall_of_fame"},
        name="coming_soon_hall_of_fame",
    ),
    # Stats-scoped (6)
    path(
        "<int:league_id>/stats/game-log/",
        views.coming_soon,
        {"feature_key": "stats_game_log"},
        name="coming_soon_game_log",
    ),
    path(
        "<int:league_id>/stats/league-leaders/",
        views.coming_soon,
        {"feature_key": "stats_league_leaders"},
        name="coming_soon_league_leaders",
    ),
    path(
        "<int:league_id>/stats/player-ratings/",
        views.coming_soon,
        {"feature_key": "stats_player_ratings"},
        name="coming_soon_player_ratings",
    ),
    path(
        "<int:league_id>/stats/player-stats/",
        views.coming_soon,
        {"feature_key": "stats_player_stats"},
        name="coming_soon_player_stats",
    ),
    path(
        "<int:league_id>/stats/team-stats/",
        views.coming_soon,
        {"feature_key": "stats_team_stats"},
        name="coming_soon_team_stats",
    ),
    path(
        "<int:league_id>/stats/statistical-feats/",
        views.coming_soon,
        {"feature_key": "stats_statistical_feats"},
        name="coming_soon_statistical_feats",
    ),
    path("", league_views.league_list, name="league_list"),
]
